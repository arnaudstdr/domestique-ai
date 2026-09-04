"""
Check du matin — décision go / alléger / repos, répercutée dans le plan.

À partir des métriques du matin (HRV, sommeil, readiness), des alertes
(overtraining + morning) et de la séance prévue du plan, le module décide si
l'athlète peut faire la séance planifiée (``go``), doit l'alléger (``adjust``)
ou doit se reposer complètement (``rest``).

La décision est persistée dans ``plan_decisions`` quand elle diffère du plan
(``adjust`` / ``rest``) : le Plan affiche alors « REPOS (coach) » ou la séance
allégée à la place de la séance prévue, et la compliance hebdomadaire
(``processing/compliance``) l'intègre (un repos décidé n'est pas manqué).

Règle d'or : le LLM n'invente jamais de chiffre. La classe de décision est
toujours fixée par les règles déterministes ci-dessous ; le LLM ne peut que
réécrire la raison et ajuster la durée allégée dans des bornes strictes.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import statistics
from typing import Any

from domestique_ai.athlete_context import AthleteContext, context_from_env
from domestique_ai.processing.plan_builder import Workout

# Seuils déterministes (cf. readiness_band dans processing/morning_metrics).
_READINESS_REST = 30.0
_READINESS_ADJUST = 50.0
_SLEEP_REST_H = 5.0
_SLEEP_REST_QUALITY_H = 5.5  # nuit très courte + qualité mauvaise → repos
_SLEEP_ADJUST_H = 6.5  # allègement dès une nuit sous 6h30
_SLEEP_QUALITY_THRESHOLD = 60  # sleep_score sous ce seuil = mauvaise qualité
_TSB_ADJUST = -10.0
# Facteur de réduction de durée en cas d'allègement.
_ADJUST_DURATION_FACTOR = 0.75
# Paliers de durée minimale par type de séance allégée.
_ADJUST_MIN_DURATION = 20

# Downgrade de type : le plan est allégé mais garde une intention.
_DOWNGRADE_KIND: dict[str, str] = {
    "intervals": "tempo",
    "tempo": "endurance",
    "endurance": "endurance",
    "recovery": "recovery",
}


def _downgraded_workout(planned: dict[str, Any], day_max_min: int | None) -> Workout:
    """Construit la séance allégée à partir de la séance planifiée."""
    from domestique_ai.processing.plan_builder import (
        _TARGET_ZONE,
        _TSS_PER_MIN,
        _name_for,
        _structure_for,
    )

    kind = _DOWNGRADE_KIND.get(planned.get("kind", "endurance"), "endurance")
    duration = int((planned.get("duration_min") or 60) * _ADJUST_DURATION_FACTOR)
    duration = max(_ADJUST_MIN_DURATION, duration)
    if day_max_min is not None:
        duration = min(duration, max(_ADJUST_MIN_DURATION, day_max_min))
    return Workout(
        date=planned["date"],
        name=_name_for(kind, duration, 0, False, False),
        sport=planned.get("sport", "cycling"),
        kind=kind,
        duration_min=duration,
        target_zone=_TARGET_ZONE.get(kind, "z2"),
        structure=_structure_for(kind, duration),
        estimated_tss=round(_TSS_PER_MIN.get(kind, 1.0) * duration, 1),
        notes=planned.get("notes", "") + " — allégé (check du matin)",
    )


def _collect_signals(today: _dt.date, ctx: AthleteContext) -> dict[str, Any]:
    """Agrège les données réelles du jour (best-effort, ne lève pas)."""
    from domestique_ai.processing.analyzer import (
        calculate_ctl_atl_tsb,
        fetch_activities_from_db,
    )
    from domestique_ai.processing.morning_metrics import (
        compute_baselines,
        detect_morning_alerts,
        fetch_morning_entry,
        readiness_band,
    )
    from domestique_ai.processing.overtraining import detect_overtraining_signals

    signals: dict[str, Any] = {
        "date": today.isoformat(),
        "morning_entry": None,
        "readiness": None,
        "readiness_band": None,
        "sleep_hours": None,
        "sleep_score": None,
        "sleep_baseline": None,
        "sleep_delta_pct": None,
        "hrv_delta_pct": None,
        "resting_hr_delta_pct": None,
        "morning_alerts": [],
        "overtraining_alerts": [],
        "critical": False,
        "tsb": None,
        "ctl": None,
        "atl": None,
    }

    try:
        entry = fetch_morning_entry(today.isoformat(), db_path=ctx.db_path)
        signals["morning_entry"] = entry
        if entry is not None:
            signals["readiness"] = entry.get("readiness_score")
            signals["readiness_band"] = readiness_band(entry.get("readiness_score"))
            signals["sleep_hours"] = entry.get("sleep_hours")
            signals["sleep_score"] = entry.get("sleep_score")
        baseline = compute_baselines("hrv_ms", db_path=ctx.db_path)
        if baseline.get("available") and baseline.get("latest_date") == today.isoformat():
            signals["hrv_delta_pct"] = round(baseline["delta_pct"], 1)
        baseline = compute_baselines("resting_hr", db_path=ctx.db_path)
        if baseline.get("available") and baseline.get("latest_date") == today.isoformat():
            signals["resting_hr_delta_pct"] = round(baseline["delta_pct"], 1)
        # Sommeil : qualité + tendance vs baseline 14 j (une seule nuit mauvaise
        # dans une semaine normale ≠ un pattern de dette de sommeil).
        if signals["sleep_hours"] is not None:
            from domestique_ai.processing.morning_metrics import fetch_morning_history

            history = fetch_morning_history(db_path=ctx.db_path)
            prior = [
                e["sleep_hours"]
                for e in history
                if e["date"] < today.isoformat()
                and e.get("sleep_hours") is not None
                and e["date"] >= (today - _dt.timedelta(days=14)).isoformat()
            ]
            if prior:
                signals["sleep_baseline"] = round(statistics.median(prior), 2)
                signals["sleep_delta_pct"] = round(
                    (signals["sleep_hours"] - signals["sleep_baseline"]) / signals["sleep_baseline"] * 100,
                    1,
                )
        for alert in detect_morning_alerts(db_path=ctx.db_path) or []:
            if alert.get("latest_date") != today.isoformat():
                continue
            signals["morning_alerts"].append(alert)
            if alert.get("severity") == "critical":
                signals["critical"] = True
    except Exception:  # noqa: BLE001
        pass

    try:
        ot = detect_overtraining_signals(ctx=ctx)
        signals["overtraining_alerts"] = ot.get("alerts", []) or []
        for alert in signals["overtraining_alerts"]:
            if alert.get("indicator") in ("tsb_chronic", "strain"):
                signals["critical"] = True
    except Exception:  # noqa: BLE001
        pass

    try:
        activities = fetch_activities_from_db(ctx=ctx)
        curves = calculate_ctl_atl_tsb(activities, end_date=today)
        if curves:
            last = curves[-1]
            signals["tsb"] = round(float(last.get("TSB", 0.0)), 1)
            signals["ctl"] = round(float(last.get("CTL", 0.0)), 1)
            signals["atl"] = round(float(last.get("ATL", 0.0)), 1)
    except Exception:  # noqa: BLE001
        pass

    return signals


def _decide_rules(signals: dict[str, Any]) -> tuple[str, str]:
    """Classe de décision déterministe + raison. Ne lève pas."""
    readiness = signals.get("readiness")
    sleep_hours = signals.get("sleep_hours")
    sleep_score = signals.get("sleep_score")
    tsb = signals.get("tsb")

    if signals.get("critical"):
        return (
            "rest",
            "Signaux de récupération critiques (alerte HRV/FC repos ou charge chronique). Repos complet recommandé.",
        )
    if readiness is not None and readiness < _READINESS_REST:
        return "rest", f"Readiness très basse ({readiness:.0f}/100). Repos complet recommandé."
    if sleep_hours is not None and sleep_hours < _SLEEP_REST_H:
        return "rest", f"Nuit très courte ({sleep_hours:.1f} h). Repos complet recommandé."
    if (
        sleep_hours is not None
        and sleep_hours < _SLEEP_REST_QUALITY_H
        and sleep_score is not None
        and sleep_score < _SLEEP_QUALITY_THRESHOLD
    ):
        return (
            "rest",
            f"Nuit très courte ({sleep_hours:.1f} h) et qualité basse "
            f"(score {sleep_score:.0f}/100). Repos complet recommandé.",
        )

    if readiness is not None and readiness < _READINESS_ADJUST:
        return "adjust", f"Readiness modérée ({readiness:.0f}/100). Séance allégée recommandée."
    if sleep_hours is not None and sleep_hours < _SLEEP_ADJUST_H:
        return "adjust", f"Nuit courte ({sleep_hours:.1f} h). Séance allégée recommandée."
    if (
        sleep_hours is not None
        and sleep_hours < 7.0
        and sleep_score is not None
        and sleep_score < _SLEEP_QUALITY_THRESHOLD
    ):
        return (
            "adjust",
            f"Qualité de sommeil basse ({sleep_score:.0f}/100, {sleep_hours:.1f} h). Séance allégée recommandée.",
        )
    if tsb is not None and tsb < _TSB_ADJUST:
        return "adjust", f"Fraîcheur dégradée (TSB {tsb:.1f}). Séance allégée recommandée."
    if signals.get("morning_alerts"):
        metric = signals["morning_alerts"][0]["metric"]
        delta = signals["morning_alerts"][0]["delta_pct"]
        return "adjust", f"Dérive détectée ({metric} {delta:+.1f} %). Séance allégée recommandée."

    return "go", "Tout est au vert — tu peux faire la séance prévue."


def _refine_reason_with_llm(decision: str, reason: str, signals: dict[str, Any]) -> str:
    """Raison rédigée par le LLM dans les bornes de la décision (best-effort)."""
    from domestique_ai.llm.ollama_client import chat_structured_sync

    schema = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "maxLength": 220},
            "message": {"type": "string", "maxLength": 220},
        },
        "required": ["reason"],
    }
    prompt = (
        "Tu es un coach cyclisme. Décision déjà arrêtée par nos règles: "
        f"{decision}. Rédige en français, en une phrase, la recommandation "
        "pour l'athlète (état + action). Données: "
        f"readiness={signals.get('readiness')}, sommeil={signals.get('sleep_hours')}h, "
        f"TSB={signals.get('tsb')}, alertes={[a['metric'] for a in signals.get('morning_alerts', [])]}. "
        "Retourne un JSON {\"reason\": str}."
    )
    try:
        result = chat_structured_sync(prompt, schema, system="Coach cycliste — bref et concret.", timeout_s=15)
        if isinstance(result, dict) and result.get("reason"):
            return str(result["reason"])[:220]
    except Exception:  # noqa: BLE001 — jamais bloquant
        pass
    return reason


def evaluate_daily_decision(
    today: _dt.date | None = None,
    *,
    ctx: AthleteContext | None = None,
    use_llm: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Évalue le check du matin pour une date (défaut: aujourd'hui).

    Retourne un dict : ``{date, decision, workout, reason, signals, source,
    plan_id, persisted}``. La décision non-``go`` est persistée dans
    ``plan_decisions`` (sauf si le jour est couvert par une prescription coach).
    """
    from domestique_ai.llm.today_cache import invalidate as invalidate_today_cache
    from domestique_ai.llm.tools import get_planned_workout

    ctx = ctx or context_from_env()
    today = today or _dt.date.today()
    signals = _collect_signals(today, ctx)

    planned = get_planned_workout(today.isoformat(), ctx=ctx)
    planned_workout = planned.get("planned_workout") if planned.get("available") else None

    if planned_workout is None:
        # Jour non programmé (repos prévu ou hors plan) : rien à décider.
        return {
            "date": today.isoformat(),
            "decision": "rest" if planned.get("available") else "go",
            "workout": None,
            "reason": (
                "Jour de repos prévu dans le plan."
                if planned.get("available")
                else "Aucune séance prévue aujourd'hui."
            ),
            "signals": signals,
            "source": "plan",
            "plan_id": planned.get("plan_id"),
            "persisted": False,
        }

    decision, reason = _decide_rules(signals)

    workout: dict[str, Any] | None = planned_workout
    if decision == "rest":
        workout = None
        if use_llm:
            reason = _refine_reason_with_llm(decision, reason, signals)
    elif decision == "adjust":
        day_max = None
        try:
            from domestique_ai.llm.availability import load_availability

            availability = load_availability(ctx.availability_path)
            if availability is not None:
                day = availability.get_for_date(today.isoformat())
                day_max = day.max_duration_min if day is not None else None
        except Exception:  # noqa: BLE001
            pass
        adjusted = _downgraded_workout(planned_workout, day_max)
        workout = adjusted.to_dict()
        if use_llm:
            reason = _refine_reason_with_llm(decision, reason, signals)
    else:
        if use_llm:
            reason = _refine_reason_with_llm(decision, reason, signals)

    persisted = False
    plan_id = planned.get("plan_id")
    if persist and decision in ("adjust", "rest") and plan_id is not None:
        from domestique_ai.llm.plan_storage import save_day_decision

        try:
            save_day_decision(
                plan_id,
                today.isoformat(),
                "adjusted" if decision == "adjust" else "rest",
                workout=Workout.from_dict(workout) if workout else None,
                reason=reason,
                decided_by="daily_check",
                db_path=ctx.db_path,
            )
            persisted = True
            with contextlib.suppress(Exception):
                invalidate_today_cache(today.isoformat(), db_path=ctx.db_path)
        except Exception:  # noqa: BLE001
            persisted = False

    return {
        "date": today.isoformat(),
        "decision": decision,
        "workout": workout,
        "reason": reason,
        "signals": signals,
        "source": "rules",
        "plan_id": plan_id,
        "persisted": persisted,
    }