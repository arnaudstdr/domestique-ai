"""
Suggestion de séance pour la date courante.

Architecture en 6 étapes (sans LLM par défaut, LLM activable et désactivable
au runtime) :

1. Cache lookup (SQLite ``today_suggestions``) — clé date + objectif + TSB.
2. Construction d'un "dossier de décision" (objectif, TSB, plan persisté, J-1,
   distribution zones semaine, signaux overtraining + matin).
3. Court-circuits : jour off → repos ; plan persisté couvre aujourd'hui →
   retour direct de la séance planifiée.
4. Tentative d'appel LLM structuré (JSON) pour décider du ``kind`` et de la
   durée, avec rationale.
5. Fallback déterministe enrichi si le LLM est indisponible ou produit un
   JSON invalide.
6. Construction du ``Workout`` + écriture cache + retour.

Exposée :
- à l'API REST (`GET /api/coach/today`) → carte « Séance du jour » du Dashboard,
- au coach LLM via le tool `propose_workout_today`.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from domestique_ai.athlete_context import AthleteContext, context_from_env
from domestique_ai.llm import today_cache
from domestique_ai.llm.availability import (
    Availability,
    AvailabilityError,
    load_availability,
)
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)
from domestique_ai.processing.plan_builder import (
    _BASE_DURATION_MIN,
    _TARGET_ZONE,
    _TSS_PER_MIN,
    Workout,
    _name_for,
    _structure_for,
)

log = logging.getLogger(__name__)

_WEEKDAY_FR: dict[int, str] = {
    0: "lundi",
    1: "mardi",
    2: "mercredi",
    3: "jeudi",
    4: "vendredi",
    5: "samedi",
    6: "dimanche",
}

# Bornes recommandées de durée de séance, indépendamment de ce que demande
# l'utilisateur : éviter une recovery de 5 min ou un endurance de 8 h.
_MIN_DURATION_MIN = 20
_MAX_DURATION_MIN = 240

_KIND_VALUES = ("recovery", "endurance", "tempo", "intervals")


# ---------------------------------------------------------------------------
# Helpers conservés (rétrocompat)
# ---------------------------------------------------------------------------


def _tsb_zone_label(tsb: float) -> str:
    """Même barème que le tool `_tsb_zone_label`, sans emoji."""
    if tsb > 5:
        return "Frais"
    if tsb >= -10:
        return "Optimal"
    if tsb >= -20:
        return "Fatigué"
    return "Surentraîné"


def _resolve_duration(
    kind: str,
    day_max_min: int | None,
    available_min: int | None,
    suggested_min: int | None = None,
) -> int:
    """Croise la durée suggérée (LLM), la dispo du jour et l'override utilisateur.

    Priorité : override explicite > suggestion LLM (bornée par la dispo) >
    base du kind plafonnée par la dispo.
    """
    base = _BASE_DURATION_MIN.get(kind, 60)
    if available_min is not None and available_min > 0:
        duration = available_min
    elif suggested_min is not None and suggested_min > 0:
        duration = suggested_min
        if day_max_min is not None:
            duration = min(duration, day_max_min)
    elif day_max_min is not None:
        duration = min(base, day_max_min)
    else:
        duration = base
    if duration < _MIN_DURATION_MIN:
        duration = _MIN_DURATION_MIN
    if duration > _MAX_DURATION_MIN:
        duration = _MAX_DURATION_MIN
    return int(duration)


def _load_availability_safely(ctx: AthleteContext) -> Availability | None:
    """Renvoie l'``Availability`` ou ``None`` si fichier absent / mal formé."""
    try:
        return load_availability(ctx.availability_path)
    except AvailabilityError:
        return None


# ---------------------------------------------------------------------------
# Construction du dossier de décision
# ---------------------------------------------------------------------------


def _infer_kind_from_zones(
    z_times: dict[str, float | None],
    avg_hr: float | None,
) -> str | None:
    """Déduit le kind dominant d'une séance à partir de ses zones HR.

    Retourne None si les zones sont absentes (séance non backfillée).
    """
    if not z_times or all(v in (None, 0) for v in z_times.values()):
        return None
    total = sum(v or 0.0 for v in z_times.values())
    if total <= 0:
        return None
    shares = {k: (v or 0.0) / total for k, v in z_times.items()}
    if shares.get("z5", 0) + shares.get("z4", 0) >= 0.20:
        return "intervals"
    if shares.get("z3", 0) >= 0.30:
        return "tempo"
    if shares.get("z1", 0) >= 0.70 and (avg_hr is None or avg_hr < 130):
        return "recovery"
    return "endurance"


def _last_session_kind(
    activities: list[dict[str, Any]],
    today: _dt.date,
) -> tuple[str | None, int | None]:
    """Renvoie (kind, days_ago) de la dernière séance avant `today`.

    Ignore les activités du jour J. Si l'historique est vide ou si la dernière
    séance n'a pas de zones renseignées, kind=None.
    """
    for act in reversed(activities):
        raw_date = act.get("date") or ""
        if not raw_date:
            continue
        try:
            d = _dt.datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d >= today:
            continue
        zones = {key: act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
        kind = _infer_kind_from_zones(zones, act.get("avg_heart_rate"))
        return kind, (today - d).days
    return None, None


def _weekly_zone_distribution(
    activities: list[dict[str, Any]],
    today: _dt.date,
    days: int = 7,
) -> dict[str, float]:
    """Renvoie les parts (0..1) de chaque zone sur la fenêtre [today-days, today)."""
    start = today - _dt.timedelta(days=days)
    totals: dict[str, float] = dict.fromkeys(HR_ZONE_KEYS, 0.0)
    for act in activities:
        raw_date = act.get("date") or ""
        if not raw_date:
            continue
        try:
            d = _dt.datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d < start or d >= today:
            continue
        for key in HR_ZONE_KEYS:
            value = act.get(f"hr_{key}_time")
            if value:
                totals[key] += float(value)
    total = sum(totals.values())
    if total <= 0:
        return dict.fromkeys(HR_ZONE_KEYS, 0.0)
    return {k: v / total for k, v in totals.items()}


def _weeks_to_event(objective: dict[str, Any] | None, today: _dt.date) -> int | None:
    """Renvoie le nombre de semaines jusqu'à la date d'objectif, ou ``None``."""
    if not objective or not objective.get("date"):
        return None
    try:
        target = _dt.date.fromisoformat(str(objective["date"]))
    except ValueError:
        return None
    delta = (target - today).days
    if delta < 0:
        return 0
    return (delta + 6) // 7


def _planned_workout_for(today: _dt.date, ctx: AthleteContext) -> dict[str, Any] | None:
    """Séance prévue exactement à `today` : prescription coach prioritaire, sinon plan."""
    # Une séance prescrite par le coach prime sur le plan généré ce jour-là.
    try:
        from domestique_ai.llm.prescription_storage import get_prescription_for_date

        prescribed = get_prescription_for_date(today.isoformat(), db_path=ctx.db_path)
        if prescribed is not None:
            return prescribed.to_dict()
    except Exception:  # noqa: BLE001 — best-effort, ne doit rien casser
        pass
    try:
        from domestique_ai.llm.plan_storage import list_plans, load_plan
    except Exception:  # noqa: BLE001 — un import qui échoue ne doit rien casser
        return None
    try:
        plans = sorted(
            list_plans(limit=20, db_path=ctx.db_path), key=lambda m: m["id"], reverse=True
        )
    except Exception:  # noqa: BLE001
        return None
    target = today.isoformat()
    for meta in plans:
        try:
            workouts = load_plan(meta["id"], db_path=ctx.db_path)
        except Exception:  # noqa: BLE001
            continue
        if not workouts:
            continue
        first = workouts[0].date
        last = workouts[-1].date
        if not (first <= target <= last):
            continue
        for w in workouts:
            if w.date == target:
                return w.to_dict()
        return None
    return None


def _alerts_summary(ctx: AthleteContext) -> dict[str, Any]:
    """Renvoie un résumé des alertes overtraining + matin (best-effort, ne lève pas)."""
    summary: dict[str, Any] = {"critical": False, "messages": []}
    try:
        from domestique_ai.processing.overtraining import detect_overtraining_signals

        report = detect_overtraining_signals(ctx=ctx)
        for alert in report.get("alerts", []) or []:
            summary["messages"].append(f"{alert.get('indicator')}: {alert.get('message')}")
            if alert.get("indicator") in ("tsb_chronic", "strain"):
                summary["critical"] = True
    except Exception:  # noqa: BLE001
        log.debug("Échec calcul overtraining", exc_info=True)
    try:
        from domestique_ai.processing.morning_metrics import detect_morning_alerts

        for alert in detect_morning_alerts(db_path=ctx.db_path) or []:
            summary["messages"].append(
                f"morning/{alert.get('metric')}: "
                f"delta {alert.get('delta_pct'):.1f}% ({alert.get('severity')})"
            )
            if alert.get("severity") == "critical":
                summary["critical"] = True
    except Exception:  # noqa: BLE001
        log.debug("Échec calcul morning alerts", exc_info=True)
    return summary


def _build_decision_dossier(
    today: _dt.date,
    availability: Availability | None,
    available_min: int | None,
    ctx: AthleteContext,
) -> dict[str, Any]:
    """Collecte toutes les données utiles à la décision dans un seul dict."""
    weekday = today.weekday()
    day = availability.get(weekday) if availability is not None else None
    intervals_day_today = (
        availability is not None
        and availability.intervals_day is not None
        and availability.intervals_day == weekday
    )

    activities = fetch_activities_from_db(ctx=ctx)
    curves = calculate_ctl_atl_tsb(activities, end_date=today)
    if curves:
        last = curves[-1]
        tsb = float(last.get("TSB", 0.0))
        ctl = float(last.get("CTL", 0.0))
        atl = float(last.get("ATL", 0.0))
    else:
        tsb = 0.0
        ctl = 0.0
        atl = 0.0

    objective_dict: dict[str, Any] | None = None
    try:
        from domestique_ai.llm.objectives import load_objective

        obj = load_objective(ctx.objective_path)
        if obj is not None:
            objective_dict = obj.to_dict()
    except Exception:  # noqa: BLE001
        objective_dict = None

    weeks_to_event = _weeks_to_event(objective_dict, today)
    planned_today = _planned_workout_for(today, ctx)
    last_kind, last_days_ago = _last_session_kind(activities, today)
    weekly_zones = _weekly_zone_distribution(activities, today, days=7)
    alerts = _alerts_summary(ctx)

    return {
        "today": today.isoformat(),
        "weekday_fr": _WEEKDAY_FR[weekday],
        "day_availability": (
            {
                "max_duration_min": day.max_duration_min,
                "context": day.context,
            }
            if day is not None
            else None
        ),
        "is_off_day": availability is not None and day is None,
        "intervals_day_today": intervals_day_today,
        "available_min": available_min,
        "tsb": round(tsb, 1),
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb_zone": _tsb_zone_label(tsb),
        "objective": objective_dict,
        "weeks_to_event": weeks_to_event,
        "planned_today": planned_today,
        "last_kind": last_kind,
        "last_days_ago": last_days_ago,
        "weekly_zone_distribution": {k: round(v, 3) for k, v in weekly_zones.items()},
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Décision (LLM + fallback déterministe)
# ---------------------------------------------------------------------------


def _decide_kind_fallback(dossier: dict[str, Any]) -> dict[str, Any]:
    """Cascade déterministe enrichie. Retourne {kind, duration_min, rationale}."""
    tsb = dossier["tsb"]
    last_kind = dossier.get("last_kind")
    last_days_ago = dossier.get("last_days_ago")
    weeks_to_event = dossier.get("weeks_to_event")
    intervals_day_today = dossier.get("intervals_day_today")
    zones = dossier.get("weekly_zone_distribution") or {}
    alerts = dossier.get("alerts") or {}

    if alerts.get("critical"):
        return {
            "kind": "recovery",
            "duration_min": None,
            "rationale": (
                "Signal critique détecté (surcharge / dérive matinale) : "
                "récupération active Z1 imposée pour rompre la fatigue."
            ),
        }

    # Taper : à 2 semaines ou moins de l'objectif, on coupe les intervalles.
    if weeks_to_event is not None and weeks_to_event <= 2 and tsb >= -10:
        return {
            "kind": "tempo",
            "duration_min": None,
            "rationale": (
                f"Taper ({weeks_to_event} sem avant objectif) : on garde un "
                "rappel d'intensité courte (Z3) et on réduit le volume."
            ),
        }

    if tsb < -10:
        return {
            "kind": "recovery",
            "duration_min": None,
            "rationale": (
                f"TSB {tsb:.1f} (zone Fatigué/Surentraîné) : "
                "récupération active Z1 pour faire baisser l'ATL."
            ),
        }

    # Anti-redondance : si on a fait du Z4-Z5 hier, pas d'intensité aujourd'hui.
    just_intensified = last_kind in ("intervals", "tempo") and (
        last_days_ago is not None and last_days_ago <= 1
    )

    if tsb > 5 and intervals_day_today and not just_intensified:
        return {
            "kind": "intervals",
            "duration_min": None,
            "rationale": (
                f"TSB {tsb:.1f} (Frais) sur jour intervalles : "
                "séance qualitative Z4 (seuil) tout indiquée."
            ),
        }
    if tsb > 5 and not just_intensified:
        return {
            "kind": "tempo",
            "duration_min": None,
            "rationale": (
                f"TSB {tsb:.1f} (Frais) hors jour intervalles : "
                "tempo Z3 pour exploiter la fraîcheur sans surcharger."
            ),
        }

    # Zone optimale ([-10, 5]) — c'est ici qu'on évite le "toujours endurance".
    z2_share = zones.get("z2", 0.0)
    intensity_share = zones.get("z3", 0.0) + zones.get("z4", 0.0) + zones.get("z5", 0.0)

    # Si on enchaîne les Z2 et qu'on est trop monotone, on injecte du tempo.
    if (
        last_kind == "endurance"
        and z2_share >= 0.65
        and intensity_share < 0.15
        and not just_intensified
        and (weeks_to_event is None or weeks_to_event >= 3)
    ):
        return {
            "kind": "tempo",
            "duration_min": None,
            "rationale": (
                f"Semaine très Z2 ({z2_share * 100:.0f}%) et peu d'intensité "
                f"({intensity_share * 100:.0f}%) : on casse la monotonie avec "
                "un tempo Z3 (polarisation 80/20)."
            ),
        }

    if just_intensified:
        return {
            "kind": "endurance",
            "duration_min": None,
            "rationale": (
                f"Hier = {last_kind} (J-{last_days_ago}) : "
                "endurance Z2 aujourd'hui pour digérer la séance qualitative."
            ),
        }

    return {
        "kind": "endurance",
        "duration_min": None,
        "rationale": (
            f"TSB {tsb:.1f} (Optimal) : foncier Z2, base aérobie qui répare ET construit."
        ),
    }


_LLM_SYSTEM_PROMPT = (
    "Tu es un coach d'endurance expert en cyclisme. Tu DOIS répondre en JSON "
    "strict avec exactement ces clés : kind, duration_min, rationale, "
    "confidence.\n\n"
    "Règles :\n"
    "- kind ∈ {recovery, endurance, tempo, intervals}\n"
    "- duration_min : entier en minutes (20..240). DOIT respecter la dispo "
    "max du jour si fournie.\n"
    "- rationale : 1 à 3 phrases en français, citer le TSB et l'objectif "
    "(weeks_to_event) quand pertinents.\n"
    "- confidence : 0.0..1.0\n\n"
    "Heuristiques :\n"
    "- TSB < -10 → recovery.\n"
    "- weeks_to_event ≤ 2 → taper (tempo court, pas d'intervalles).\n"
    "- Hier était intervals/tempo → privilégier endurance (récup active).\n"
    "- Semaine déjà très Z2 (>65%) et peu d'intensité (<15%) → tempo "
    "(polarisation 80/20).\n"
    "- TSB > 5 + intervals_day_today → intervals.\n"
    "- TSB > 5 sinon → tempo.\n"
    "- Sinon → endurance, sauf cas listés ci-dessus.\n"
    "- Une alerte critique force recovery.\n"
)


def _decide_kind_with_llm(dossier: dict[str, Any]) -> dict[str, Any] | None:
    """Demande au LLM de choisir kind + durée + rationale en JSON.

    Retourne None si Ollama est indisponible ou si le JSON est invalide.
    """
    from domestique_ai.llm.ollama_client import chat_structured_sync

    user_payload = json.dumps(dossier, ensure_ascii=False, default=str)
    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Voici le dossier de décision du jour. Renvoie UNIQUEMENT le "
                "JSON demandé.\n\n" + user_payload
            ),
        },
    ]
    response = chat_structured_sync(messages, timeout_s=20.0)
    if response is None:
        return None

    kind = response.get("kind")
    duration = response.get("duration_min")
    rationale = response.get("rationale")
    confidence = response.get("confidence", 0.5)

    if kind not in _KIND_VALUES:
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    try:
        duration_int = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None
    if duration_int is not None and not (_MIN_DURATION_MIN <= duration_int <= _MAX_DURATION_MIN):
        duration_int = None
    try:
        confidence_f = float(confidence)
    except (TypeError, ValueError):
        confidence_f = 0.5

    return {
        "kind": kind,
        "duration_min": duration_int,
        "rationale": rationale.strip(),
        "confidence": confidence_f,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _build_workout_payload(
    today: _dt.date,
    dossier: dict[str, Any],
    decision: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """Construit le dict final retourné (workout structuré + métadonnées)."""
    kind = decision["kind"]
    day_avail = dossier.get("day_availability") or {}
    duration_min = _resolve_duration(
        kind,
        day_avail.get("max_duration_min"),
        dossier.get("available_min"),
        decision.get("duration_min"),
    )
    structure = _structure_for(kind, duration_min)
    target_zone = _TARGET_ZONE[kind]
    estimated_tss = round(_TSS_PER_MIN[kind] * duration_min, 1)
    notes_parts: list[str] = []
    context = day_avail.get("context")
    if context:
        notes_parts.append(str(context).capitalize())
    workout = Workout(
        date=today.isoformat(),
        name=_name_for(kind, duration_min, week_idx=0, is_taper=False, is_recovery_week=False),
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone=target_zone,
        structure=structure,
        estimated_tss=estimated_tss,
        notes=" — ".join(notes_parts),
    )

    signals = {
        "tsb": dossier["tsb"],
        "ctl": dossier["ctl"],
        "atl": dossier["atl"],
        "weeks_to_event": dossier.get("weeks_to_event"),
        "last_kind": dossier.get("last_kind"),
        "last_days_ago": dossier.get("last_days_ago"),
        "weekly_zone_distribution": dossier.get("weekly_zone_distribution"),
        "alerts": dossier.get("alerts", {}).get("messages", []),
    }
    if "confidence" in decision:
        signals["llm_confidence"] = decision["confidence"]
        if decision["confidence"] < 0.4:
            signals["low_confidence"] = True

    return {
        "rest_day": False,
        "workout": workout.to_dict(),
        "tsb": dossier["tsb"],
        "tsb_zone": dossier["tsb_zone"],
        "rationale": decision.get("rationale", ""),
        "signals": signals,
        "source": source,
    }


def _planned_workout_to_payload(
    today: _dt.date,
    dossier: dict[str, Any],
    planned: dict[str, Any],
) -> dict[str, Any]:
    """Adapte une séance issue du plan persisté au format de retour."""
    available_min = dossier.get("available_min")
    if available_min and available_min > 0:
        # L'utilisateur impose une durée → on respecte mais on garde le kind.
        return _build_workout_payload(
            today,
            dossier,
            {
                "kind": planned.get("kind", "endurance"),
                "duration_min": available_min,
                "rationale": ("Séance du plan persisté, durée ajustée selon le paramètre fourni."),
            },
            source="plan",
        )
    return {
        "rest_day": False,
        "workout": planned,
        "tsb": dossier["tsb"],
        "tsb_zone": dossier["tsb_zone"],
        "rationale": (
            "Séance prévue dans le plan d'entraînement en cours pour cette date — on s'y tient."
        ),
        "signals": {
            "tsb": dossier["tsb"],
            "ctl": dossier["ctl"],
            "atl": dossier["atl"],
            "weeks_to_event": dossier.get("weeks_to_event"),
            "alerts": dossier.get("alerts", {}).get("messages", []),
        },
        "source": "plan",
    }


def propose_workout_today(
    today: _dt.date | None = None,
    available_min: int | None = None,
    refresh: bool = False,
    use_llm: bool = True,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Propose une séance pour aujourd'hui.

    Retourne soit :
    - ``{"rest_day": True, "reason": ...}`` quand le weekday n'est pas listé
      dans ``data/availability.yaml`` (et qu'aucun override ``available_min``
      n'est passé),
    - ``{"rest_day": False, "workout": <Workout.to_dict()>, "tsb": float,
       "tsb_zone": str, "rationale": str, "signals": dict, "source": str}``
      sinon, où ``source`` ∈ {``"cache"``, ``"llm"``, ``"fallback"``,
      ``"plan"``}.

    Paramètres :
    - ``today`` : date à utiliser (par défaut ``date.today()``). Injectable pour
      les tests déterministes.
    - ``available_min`` : durée explicite (override de la dispo du jour).
    - ``refresh`` : si ``True``, bypass le cache et regénère.
    - ``use_llm`` : si ``False``, saute directement au fallback déterministe
      (utile pour tests / mode hors-ligne).
    """
    ctx = ctx or context_from_env()
    target = today or _dt.date.today()
    availability = _load_availability_safely(ctx)

    # Jour off (et pas d'override explicite) → repos.
    if (
        availability is not None
        and availability.get(target.weekday()) is None
        and available_min is None
    ):
        return {
            "rest_day": True,
            "reason": (
                f"{_WEEKDAY_FR[target.weekday()].capitalize()} n'est pas "
                "listé dans ta disponibilité — repos prévu."
            ),
        }

    dossier = _build_decision_dossier(target, availability, available_min, ctx)

    obj_hash = today_cache.objective_hash(dossier.get("objective"))
    tsb_key = today_cache.round_tsb(dossier["tsb"])
    cache_key = (target.isoformat(), obj_hash, tsb_key)

    if not refresh:
        cached = today_cache.load(*cache_key, db_path=ctx.db_path)
        if cached is not None:
            cached["source"] = "cache"
            return cached

    # Court-circuit plan persisté : si une séance est prévue exactement
    # aujourd'hui, on la respecte (priorité à l'intention de l'athlète).
    planned = dossier.get("planned_today")
    if planned and not dossier.get("alerts", {}).get("critical"):
        payload = _planned_workout_to_payload(target, dossier, planned)
        today_cache.save(*cache_key, payload, source="plan", db_path=ctx.db_path)
        return payload

    decision: dict[str, Any] | None = None
    source = "fallback"
    if use_llm:
        decision = _decide_kind_with_llm(dossier)
        if decision is not None:
            source = "llm"

    if decision is None:
        decision = _decide_kind_fallback(dossier)

    payload = _build_workout_payload(target, dossier, decision, source=source)
    today_cache.save(*cache_key, payload, source=source, db_path=ctx.db_path)
    return payload


__all__ = ["propose_workout_today"]
