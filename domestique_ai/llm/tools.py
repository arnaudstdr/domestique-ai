"""
Tools exposés au coach LLM via tool calling Ollama.

Chaque tool est composé de deux choses :
- un schéma JSON conforme au format OpenAI/Ollama (description + paramètres),
- une fonction Python pure qui prend les arguments désérialisés et renvoie
  un dict JSON-sérialisable.

Le LLM ne reçoit que des données déjà calculées par notre code Python — il ne
peut pas inventer des chiffres (CTL, TSB, zones), seulement les commenter.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from domestique_ai.athlete_context import AthleteContext, context_from_env
from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)

# Plafonds de sortie des tools : bornent le contexte réinjecté à chaque
# itération de la boucle de tool-calling (les résultats s'accumulent).
_MAX_RECENT_ACTIVITIES = 10
_MAX_SIMILAR_ACTIVITIES = 20
_MAX_SEARCH_CHARS = 400


def _tsb_zone_label(tsb: float) -> str:
    """Mêmes seuils que dashboard._tsb_zone_label, sans emoji."""
    if tsb > 5:
        return "Frais"
    if tsb >= -10:
        return "Optimal"
    if tsb >= -20:
        return "Fatigué"
    return "Surentraîné"


def _today() -> dt.date:
    """Indirection sur `dt.date.today()` pour monkeypatching dans les tests."""
    return dt.date.today()


def _filter_recent(
    activities: list[dict[str, Any]],
    days: int,
    *,
    end: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Active la fenêtre ``[end - (days - 1) ; end]`` (incluse), ancrée sur
    ``today()`` par défaut.

    Régression CR-006 : l'ancrage était auparavant sur la dernière activité
    en base, ce qui faisait répondre le coach LLM sur une fenêtre obsolète
    après une période sans synchronisation.
    """
    if not activities or days <= 0:
        return []
    if end is None:
        end = _today()
    # Sémantique préservée vs version pré-CR-006 : fenêtre [end - days ; end]
    # (bornes incluses), donc l'argument `days` désigne l'âge max relatif.
    start = end - dt.timedelta(days=days)
    out = []
    for act in activities:
        d = act.get("date")
        if not d:
            continue
        when = dt.datetime.fromisoformat(d.replace("Z", "+00:00")).date()
        if start <= when <= end:
            out.append(act)
    return out


def get_training_load_state(*, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """État courant CTL/ATL/TSB + zone interprétative (Frais/Optimal/Fatigué/Surentraîné)."""
    activities = fetch_activities_from_db(ctx=ctx)
    curves = calculate_ctl_atl_tsb(activities, end_date=dt.date.today())
    if not curves:
        return {"available": False, "reason": "Aucune activité en base."}
    last = curves[-1]
    return {
        "available": True,
        "date": last["date"],
        "ctl": last["CTL"],
        "atl": last["ATL"],
        "tsb": last["TSB"],
        "zone": _tsb_zone_label(last["TSB"]),
        "interpretation": {
            "ctl": "Forme à long terme (charge moyenne 42 jours).",
            "atl": "Fatigue récente (charge moyenne 7 jours).",
            "tsb": "Fraîcheur (CTL - ATL). Positif = frais, négatif = fatigué.",
        },
    }


def get_recent_activities(days: int = 7, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Liste des activités sur les N derniers jours (fenêtre ancrée sur today).

    La sortie est plafonnée à ``_MAX_RECENT_ACTIVITIES`` (les plus récentes) :
    les résultats de tools sont réinjectés à chaque itération de la boucle.
    """
    activities = fetch_activities_from_db(ctx=ctx)
    as_of = _today()
    recent = _filter_recent(activities, days, end=as_of)
    total_in_window = len(recent)
    if total_in_window > _MAX_RECENT_ACTIVITIES:
        recent = sorted(recent, key=lambda act: act.get("date") or "")[-_MAX_RECENT_ACTIVITIES:]
    out = []
    for act in recent:
        notes = act.get("notes")
        if isinstance(notes, str) and len(notes) > 200:
            notes = notes[:200]
        out.append(
            {
                "date": act.get("date"),
                "sport_type": act.get("sport_type"),
                "duration_sec": act.get("duration"),
                "distance_km": round((act.get("distance") or 0) / 1000, 2),
                "elevation_m": act.get("elevation_gain"),
                "avg_heart_rate": act.get("avg_heart_rate"),
                "max_heart_rate": act.get("max_heart_rate"),
                "avg_power": act.get("avg_power"),
                "training_load": act.get("training_load"),
                "rpe": act.get("rpe"),
                "notes": notes,
                "hr_zones_sec": {key: act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS},
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "count": len(out),
        "total_in_window": total_in_window,
        "activities": out,
    }


def get_zone_distribution(days: int = 14, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Répartition cumulée du temps par zone HR sur les N derniers jours."""
    activities = fetch_activities_from_db(ctx=ctx)
    as_of = _today()
    recent = _filter_recent(activities, days, end=as_of)
    totals: dict[str, float] = dict.fromkeys(HR_ZONE_KEYS, 0.0)
    counted = 0
    for act in recent:
        zones = {key: act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
        if any(v is None for v in zones.values()):
            continue
        for key, value in zones.items():
            totals[key] += value or 0.0
        counted += 1
    total = sum(totals.values())
    distribution = {
        key: {
            "seconds": round(value, 1),
            "minutes": round(value / 60, 1),
            "share_pct": round((value / total * 100) if total else 0.0, 1),
        }
        for key, value in totals.items()
    }
    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "activities_with_zones": counted,
        "activities_total_in_window": len(recent),
        "total_seconds": round(total, 1),
        "distribution": distribution,
    }


def get_training_trends(
    period: str = "6m",
    weeks: int = 12,
    include_ftp_projection: bool = True,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Évolution de l'athlète : courbe CTL/ATL/TSB, agrégats mensuels, volume
    hebdomadaire et projection FTP.

    Agrège ``processing.trends`` (mêmes calculs que la page « Tendances ») —
    périodes ``3m`` / ``6m`` / ``1y`` / ``all``.
    """
    from domestique_ai.processing import trends as _trends

    ctx = ctx or context_from_env()
    if period not in {"3m", "6m", "1y", "all"}:
        period = "6m"
    weeks = max(1, min(int(weeks), 52))
    today = _today()
    result: dict[str, Any] = {
        "as_of": today.isoformat(),
        "trends": _trends.get_trends(period, today=today, ctx=ctx),
        "weekly_volume": _trends.get_weekly_volume(weeks=weeks, today=today, ctx=ctx),
    }
    if include_ftp_projection:
        result["ftp_projection"] = _trends.get_ftp_projection(today=today, ctx=ctx)
    return result


def get_best_efforts(
    period: str = "1y",
    duration_min: int | None = None,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Records de puissance : meilleurs efforts 5 s → 60 min sur la période.

    ``duration_min`` cible une durée (top efforts + meilleur par année) ; sans
    lui, un record par durée standard + tendance seuil (20 min) par année.
    Nécessite les streams persistés (backfill Garmin) avec puissance.
    """
    from domestique_ai.processing.records import records_report

    ctx = ctx or context_from_env()
    return records_report(period=period, duration_min=duration_min, ctx=ctx)


def get_climb_stats(
    name: str | None = None,
    limit: int = 10,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Montées détectées (cols/bosses récurrents) : passages, meilleur temps,
    temps moyen, VAM, historique par année.

    ``name`` filtre une montée nommée (ex. « Haut-Koenigsbourg ») ; sans nom,
    liste les montées les plus grimpées. Délègue à ``processing.climbs``
    (streams persistés — backfill Garmin requis pour l'historique).
    """
    from domestique_ai.processing.climbs import climb_report

    ctx = ctx or context_from_env()
    return climb_report(name=name, limit=limit, ctx=ctx)


def get_activity_mix(
    days: int = 28,
    group_by: str = "sport",
    include_monthly: bool = False,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Mix de la pratique : par sport (défaut), type de séance (`kind`, zones
    HR) ou indoor/outdoor, sur les N derniers jours.

    ``include_monthly`` ajoute l'évolution mensuelle (mois × type). Délègue à
    ``processing.activity_stats`` ; ``kind`` est déduit des zones HR des
    activités passées (``unknown`` quand non ventilées).
    """
    from domestique_ai.processing.activity_stats import get_activity_mix_stats

    ctx = ctx or context_from_env()
    return get_activity_mix_stats(
        days=days,
        group_by=group_by,
        include_monthly=include_monthly,
        today=_today(),
        ctx=ctx,
    )


def get_objective(*, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Objectif d'entraînement courant (ou indication s'il est absent)."""
    from domestique_ai.llm.objectives import load_objective

    ctx = ctx or context_from_env()
    obj = load_objective(ctx.objective_path)
    if obj is None:
        return {
            "available": False,
            "reason": "Aucun fichier data/objective.yaml. "
            "Copier data/objective.yaml.example pour en créer un.",
        }
    return {"available": True, "objective": obj.to_dict()}


def get_profile(*, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Profil de l'athlète : niveau, FTP, W/kg, FC (repos/max), sexe, seuil
    lactique et zones HR en bpm (convention %HRR Karvonen).
    """
    from domestique_ai.processing.analyzer import hr_zone_bpm_ranges
    from domestique_ai.processing.morning_metrics import (
        latest_weight,
        power_to_weight,
    )

    ctx = ctx or context_from_env()
    weight_kg = latest_weight(db_path=ctx.db_path)
    zones = (
        hr_zone_bpm_ranges(float(ctx.hr_rest), float(ctx.hr_max))
        if ctx.hr_rest and ctx.hr_max
        else {}
    )
    return {
        "level": ctx.level,
        "ftp_w": ctx.ftp,
        "weight_kg": weight_kg,
        "wkg": power_to_weight(ctx.ftp, weight_kg),
        "hr_rest": ctx.hr_rest,
        "hr_max": ctx.hr_max,
        "sex": ctx.sex,
        "lthr_pct": ctx.lthr_pct,
        "hr_zones_bpm": zones or None,
    }


def get_activity_details(external_id: int, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Détail complet d'une activité identifiée par son id externe.

    Inclut la température (``avg_temp`` / ``min_temp`` / ``max_temp`` en °C)
    quand le stream était disponible à l'ingestion, plus les champs enrichis
    du payload liste Garmin (kcal, cadence, vitesse, D−, puissance max).
    Utile pour expliquer une dérive HR par la chaleur ou justifier un effort
    ressenti élevé.
    """
    import sqlite3

    ctx = ctx or context_from_env()
    init_db(ctx.db_path)
    conn = sqlite3.connect(ctx.db_path)
    try:
        # Id externe : strava_id (legacy), garmin_id, sinon id local (manual/TCX).
        cursor = conn.execute(
            "SELECT coalesce(strava_id, garmin_id, id), date, duration, avg_heart_rate, "
            "max_heart_rate, avg_power, elevation_gain, distance, training_load, "
            "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time, "
            "avg_temp, min_temp, max_temp, "
            "name, calories, max_power, cadence_avg, cadence_max, "
            "speed_avg, speed_max, elevation_loss, sport_type, id "
            "FROM activities WHERE strava_id = ? OR garmin_id = ? OR id = ?",
            (external_id, external_id, external_id),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return {"available": False, "external_id": external_id}
    decoupling: float | None = None
    try:
        from domestique_ai.ingestion.db import load_activity_streams
        from domestique_ai.processing.records import decoupling_pct

        payload = load_activity_streams(row[26], db_path=ctx.db_path)
        if payload:
            decoupling = decoupling_pct(payload)
    except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
        decoupling = None
    speed_avg = row[22]
    speed_max = row[23]
    return {
        "available": True,
        "external_id": row[0],
        "name": row[17],
        "sport_type": row[25],
        "date": row[1],
        "duration_sec": row[2],
        "avg_heart_rate": row[3],
        "max_heart_rate": row[4],
        "avg_power": row[5],
        "max_power": row[19],
        "elevation_m": row[6],
        "elevation_loss_m": row[24],
        "distance_km": round((row[7] or 0) / 1000, 2),
        "training_load": row[8],
        "hr_zones_sec": {HR_ZONE_KEYS[i]: row[9 + i] for i in range(5)},
        "avg_temp_c": row[14],
        "min_temp_c": row[15],
        "max_temp_c": row[16],
        "calories_kcal": row[18],
        "cadence_avg": row[20],
        "cadence_max": row[21],
        "speed_avg_kmh": round(speed_avg * 3.6, 1) if speed_avg is not None else None,
        "speed_max_kmh": round(speed_max * 3.6, 1) if speed_max is not None else None,
        "decoupling_pct": decoupling,
    }


def get_morning_trends(days: int = 30, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """
    Tendances des métriques matinales (HRV, FC repos, sommeil, stress, readiness,
    SpO2, fréquence respiratoire, température cutanée, pas, calories, poids) avec
    baselines mobiles sur 14 jours et alertes si dérive vs baseline. Expose aussi
    le poids courant et le rapport poids/puissance (W/kg) dérivé de la FTP.
    """
    from domestique_ai.processing.morning_metrics import (
        METRIC_COLUMNS,
        compute_baselines,
        detect_morning_alerts,
        fetch_morning_history,
        readiness_band,
        stress_band,
    )

    ctx = ctx or context_from_env()
    history = fetch_morning_history(days=days, db_path=ctx.db_path)
    if not history:
        return {
            "available": False,
            "reason": "Aucune métrique matinale saisie. "
            "Onglet « 🌅 Matin » du dashboard pour les enregistrer.",
        }
    baselines = {}
    for metric in METRIC_COLUMNS:
        b = compute_baselines(metric, db_path=ctx.db_path)
        if b.get("available"):
            # Sortie compacte : `sample_size` (métadonnée non citable) est
            # écarté et la baseline arrondie à 1 décimale — le coach cite
            # `latest`/`delta_pct`/alertes, pas le nombre de points.
            baselines[metric] = {
                "baseline_14d": round(b["baseline"], 1),
                "latest": b["latest"],
                "latest_date": b["latest_date"],
                "delta_pct": round(b["delta_pct"], 1),
            }

    from domestique_ai.processing.morning_metrics import (
        latest_weight,
        power_to_weight,
    )

    latest = history[-1]
    weight_kg = latest.get("weight_kg") or latest_weight(db_path=ctx.db_path)
    advanced_latest = {
        "readiness_score": latest.get("readiness_score"),
        "readiness_band": readiness_band(latest.get("readiness_score")),
        "stress_score": latest.get("stress_score"),
        "stress_band": stress_band(latest.get("stress_score")),
        "spo2_avg_pct": latest.get("spo2_avg_pct"),
        "respiratory_rate_avg_bpm": latest.get("respiratory_rate_avg_bpm"),
        "skin_temp_delta_c": latest.get("skin_temp_delta_c"),
        "steps": latest.get("steps"),
        "active_calories": latest.get("active_calories"),
        "weight_kg": weight_kg,
        "wkg": power_to_weight(ctx.ftp, weight_kg),
        "sleep_stages_min": {
            "deep": latest.get("sleep_deep_min"),
            "rem": latest.get("sleep_rem_min"),
            "light": latest.get("sleep_light_min"),
            "awake": latest.get("sleep_awake_min"),
        },
        "sleep_score_computed": latest.get("sleep_score_computed"),
        "stress_score_computed": latest.get("stress_score_computed"),
        "garmin_sleep_score": latest.get("garmin_sleep_score"),
        "garmin_readiness_score": latest.get("garmin_readiness_score"),
        "garmin_body_battery_min": latest.get("garmin_body_battery_min"),
        "garmin_body_battery_max": latest.get("garmin_body_battery_max"),
    }

    recent_days = [
        {
            "date": entry.get("date"),
            "readiness_score": entry.get("readiness_score"),
            "hrv_ms": entry.get("hrv_ms"),
            "resting_hr": entry.get("resting_hr"),
            "sleep_hours": entry.get("sleep_hours"),
        }
        for entry in history[-7:]
    ]

    return {
        "available": True,
        "days": days,
        "entries_count": len(history),
        "latest_date": latest.get("date"),
        "baselines": baselines,
        "latest_advanced": advanced_latest,
        "recent_days": recent_days,
        "alerts": detect_morning_alerts(db_path=ctx.db_path),
    }


def get_nutrition_context(days: int = 14, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Contexte factuel pour des conseils nutrition personnalisés.

    Renvoie les faits disponibles sur l'athlète (poids, FTP, W/kg, charge
    hebdo, volume et intensité des séances récentes, séance la plus longue et
    la plus dure, température moyenne, calories estimées). **Aucune donnée
    d'apport alimentaire n'est suivie** : le coach s'en sert comme ancrage
    factuel, pas pour calculer un bilan calorique.
    """
    from domestique_ai.processing.morning_metrics import latest_weight_entry, power_to_weight

    ctx = ctx or context_from_env()
    entry = latest_weight_entry(db_path=ctx.db_path)
    weight_kg = entry[1] if entry else None
    weight_date = entry[0] if entry else None
    wkg = power_to_weight(ctx.ftp, weight_kg)

    activities = fetch_activities_from_db(ctx=ctx)
    as_of = _today()
    recent = _filter_recent(activities, days, end=as_of)
    week = _filter_recent(activities, 7, end=as_of)

    def _session(act: dict[str, Any]) -> dict[str, Any]:
        load = act.get("training_load")
        return {
            "date": act.get("date"),
            "sport_type": act.get("sport_type"),
            "duration_min": round((act.get("duration") or 0) / 60),
            # Charge arrondie à l'entier : le coach n'a pas besoin de la
            # décimale pour ancrer son conseil (sortie plus compacte).
            "training_load": round(load) if isinstance(load, (int, float)) else None,
        }

    longest = max(recent, key=lambda a: a.get("duration") or 0, default=None)
    hardest = max(recent, key=lambda a: a.get("training_load") or 0, default=None)
    temps = [a.get("avg_temp") for a in recent if a.get("avg_temp") is not None]
    avg_temp = round(sum(temps) / len(temps), 1) if temps else None
    total_seconds = sum(a.get("duration") or 0 for a in recent)

    return {
        "available": True,
        "as_of": as_of.isoformat(),
        "window_days": days,
        "weight_kg": weight_kg,
        "weight_date": weight_date,
        "ftp_w": ctx.ftp,
        "wkg": wkg,
        "weekly_tss_7d": round(sum(a.get("training_load") or 0 for a in week), 1),
        "sessions_count": len(recent),
        "total_duration_h": round(total_seconds / 3600, 1),
        "longest_session": _session(longest) if longest else None,
        "hardest_session": _session(hardest) if hardest else None,
        "avg_temp_c": avg_temp,
        "total_calories_kcal": round(sum(a.get("calories") or 0 for a in recent)) or None,
        "food_log_available": False,
    }


def get_overtraining_signals(*, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """
    Indicateurs auto de surentraînement : TSB chronique, monotony de Foster,
    strain de Foster, saut de volume hebdo. Alertes agrégées.
    """
    from domestique_ai.processing.overtraining import detect_overtraining_signals

    return detect_overtraining_signals(ctx=ctx)


_WORKOUT_TEMPLATES = {
    "recovery": {
        "kind": "recovery",
        "structure": [
            {"phase": "ride", "zone": "z1", "fraction": 1.0},
        ],
        "rationale": "Sortie de récupération active : Z1 strict, cadence libre, "
        "pas de bosse. Favorise la circulation sans charger.",
    },
    "endurance": {
        "kind": "endurance",
        "structure": [
            {"phase": "warmup", "zone": "z1", "fraction": 0.10},
            {"phase": "ride", "zone": "z2", "fraction": 0.80},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.10},
        ],
        "rationale": "Foncier : long en Z2, base aérobie, capillarisation. "
        "Tu peux ajouter quelques relances courtes hors compteur.",
    },
    "tempo": {
        "kind": "tempo",
        "structure": [
            {"phase": "warmup", "zone": "z1", "fraction": 0.15},
            {"phase": "tempo", "zone": "z3", "fraction": 0.65},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.20},
        ],
        "rationale": "Tempo soutenu : Z3 continu pour résistance aérobie. "
        "À placer quand TSB > -10.",
    },
    "threshold": {
        "kind": "intervals_threshold",
        "structure": [
            {"phase": "warmup", "zone": "z2", "fraction": 0.20},
            {
                "phase": "intervals",
                "block": {"work_min": 8, "work_zone": "z4", "rest_min": 4, "rest_zone": "z2"},
                "fraction_total": 0.65,
            },
            {"phase": "cooldown", "zone": "z1", "fraction": 0.15},
        ],
        "rationale": "Seuil : intervalles 8' Z4 / 4' Z2. Ajuster nb de reps "
        "selon durée totale (60 min ≈ 3 reps, 90 min ≈ 4-5 reps).",
    },
    "vo2max": {
        "kind": "intervals_vo2max",
        "structure": [
            {"phase": "warmup", "zone": "z2", "fraction": 0.25},
            {
                "phase": "intervals",
                "block": {"work_min": 3, "work_zone": "z5", "rest_min": 3, "rest_zone": "z1"},
                "fraction_total": 0.55,
            },
            {"phase": "cooldown", "zone": "z1", "fraction": 0.20},
        ],
        "rationale": "VO2max : 3' Z5 / 3' Z1. Très exigeant, à placer "
        "quand TSB > 0 et avec récup ≥ 48 h ensuite.",
    },
}


def _kind_for_target(target_zone: str) -> str:
    return {
        "z1": "recovery",
        "z2": "endurance",
        "z3": "tempo",
        "z4": "threshold",
        "z5": "vo2max",
    }.get(target_zone, "endurance")


# Templates d'activités hors vélo (renfo, gainage, mobilité, cross-training).
# Structure en blocs travail/repos (pas de zones %HRR vélo) — accessibles par
# ``propose_workout(sport=..., target_zone=None)``.
_OFFBIKE_TEMPLATES = {
    "musculation/gainage": {
        "kind": "strength_gainage",
        "sport": "musculation/gainage",
        "structure": [
            {"phase": "warmup", "fraction": 0.20, "detail": "mobilité + activation"},
            {
                "phase": "circuit",
                "block": {"work_min": 3, "rest_min": 1},
                "fraction_total": 0.60,
                "detail": "squats, gainage ventral/latéral, pont fessier, bird-dog — 2 à 3 tours",
            },
            {"phase": "cooldown", "fraction": 0.20, "detail": "étirements"},
        ],
        "rationale": "Renfo/gainage au poids du corps ou charges légères : "
        "travail de la sangle abdominale et des chaînes postérieures, sans "
        "impact sur les jambes. Nuit pas au vélo si placé en fin de journée.",
    },
    "cross-training": {
        "kind": "cross_training",
        "sport": "cross-training",
        "structure": [
            {"phase": "warmup", "fraction": 0.15},
            {"phase": "active", "fraction": 0.70, "detail": "RPE 5-6/10, effort continu"},
            {"phase": "cooldown", "fraction": 0.15},
        ],
        "rationale": "Cross-training (elliptique, natation, càp souple, home "
        "trainer facile) : entretient le foncier en changeant les appuis et "
        "en réduisant la sollicitation spécifique.",
    },
    "mobilite": {
        "kind": "mobility",
        "sport": "mobilite",
        "structure": [
            {
                "phase": "active",
                "fraction": 1.0,
                "detail": "enchaînement mobilité "
                "hanches, thoracique, chevilles — 2 à 3 séries de 30-60 s",
            },
        ],
        "rationale": "Mobilité/récupération active : séance courte et douce, "
        "idéale en J+1 de séance dure ou en entretien régulier.",
    },
}


def propose_workout(
    target_zone: str | None = None,
    duration_min: int | None = None,
    kind: str | None = None,
    sport: str = "cyclisme",
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """
    Squelette de séance.

    Vélo (``sport="cyclisme"``, défaut) : ``target_zone`` (z1..z5) et
    ``duration_min`` requis ; ``kind`` déduit de la zone si absent.
    Hors vélo (``sport`` renfo/cross-training/mobilité) : ``target_zone``
    inutile, ``duration_min`` suffit.

    ``ctx`` est accepté pour l'appel uniforme via ``dispatch`` mais ignoré
    (séance template, sans données athlète).
    """
    if duration_min is None or duration_min <= 0:
        return {"available": False, "reason": "duration_min doit être positif."}

    is_cycling = sport in ("cyclisme", "cycling", "bike", "velo", "vélo")
    if not is_cycling:
        offbike = _OFFBIKE_TEMPLATES.get(sport)
        if offbike is None:
            return {
                "available": False,
                "reason": f"sport inconnu: {sport!r}. Attendu 'cyclisme' ou "
                f"{sorted(_OFFBIKE_TEMPLATES)}.",
            }
        return {
            "available": True,
            "sport": offbike["sport"],
            "duration_min": duration_min,
            "kind": offbike["kind"],
            "structure": offbike["structure"],
            "rationale": offbike["rationale"],
        }

    target_zone = (target_zone or "").lower()
    if target_zone not in HR_ZONE_KEYS:
        return {
            "available": False,
            "reason": f"target_zone invalide: {target_zone!r}. Attendu: {list(HR_ZONE_KEYS)}",
        }

    selected_kind = kind or _kind_for_target(target_zone)
    template = _WORKOUT_TEMPLATES.get(selected_kind)
    if template is None:
        return {
            "available": False,
            "reason": f"kind inconnu: {selected_kind!r}. Attendu: {sorted(_WORKOUT_TEMPLATES)}",
        }

    return {
        "available": True,
        "sport": "cyclisme",
        "target_zone": target_zone,
        "duration_min": duration_min,
        "kind": template["kind"],
        "structure": template["structure"],
        "rationale": template["rationale"],
    }


def generate_training_plan(
    sessions_per_week: int = 4, focus: str | None = None, *, ctx: AthleteContext | None = None
) -> dict[str, Any]:
    """
    Génère un plan d'entraînement multi-semaines jusqu'à la date inscrite dans
    ``data/objective.yaml`` (fallback : 4 semaines à partir d'aujourd'hui).

    Persiste le plan en base et retourne un summary structuré que le coach
    peut commenter. Les fichiers `.FIT` sont produits à la demande côté UI
    (téléchargement) — pas dans ce tool, qui reste rapide pour le LLM.
    """
    from domestique_ai.llm.availability import AvailabilityError
    from domestique_ai.llm.plan_storage import (
        PlanGenerationError,
        build_and_save_plan,
    )

    ctx = ctx or context_from_env()
    try:
        plan_id, plan, plan_ctx = build_and_save_plan(
            sessions_per_week=sessions_per_week,
            focus=focus,
            ctx=ctx,
        )
    except PlanGenerationError as exc:
        return {"available": False, "reason": str(exc)}
    except AvailabilityError as exc:
        return {"available": False, "reason": f"availability.yaml invalide: {exc}"}

    # Synthèse hebdomadaire : TSS prévu, durée totale, nb de séances par semaine.
    weekly: dict[str, dict[str, float]] = {}
    for w in plan:
        d = dt.date.fromisoformat(w.date)
        # Clé = lundi de la semaine ISO de la séance.
        monday = d - dt.timedelta(days=d.weekday())
        key = monday.isoformat()
        bucket = weekly.setdefault(key, {"tss": 0.0, "duration_min": 0, "sessions": 0})
        bucket["tss"] += w.estimated_tss
        bucket["duration_min"] += w.duration_min
        bucket["sessions"] += 1

    weekly_summary = [
        {
            "week_starting": key,
            "tss": round(bucket["tss"], 1),
            "duration_min": int(bucket["duration_min"]),
            "sessions": int(bucket["sessions"]),
        }
        for key, bucket in sorted(weekly.items())
    ]
    peak_week = max(weekly_summary, key=lambda w: w["tss"]) if weekly_summary else None
    custom_hr = bool(ctx.hr_rest and ctx.hr_max)

    return {
        "available": True,
        "plan_id": plan_id,
        "sessions_count": len(plan),
        "total_weeks": len(weekly_summary),
        "target_date": plan_ctx["target_date"],
        "target_event_type": plan_ctx["target_event_type"],
        "ctl_current": plan_ctx["ctl_current"],
        "weekly": weekly_summary,
        "peak_week": peak_week,
        "first_session": plan[0].to_dict(),
        "last_session": plan[-1].to_dict(),
        "fit_export_mode": "bpm_custom" if custom_hr else "garmin_zones",
        "availability_loaded": plan_ctx["availability_loaded"],
        "days_used": plan_ctx["days_used"],
        "note": "Téléchargement .ZIP des fichiers .FIT depuis l'onglet « 📋 Plan ».",
    }


def get_planned_workout(date: str, *, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """
    Séance prévue à une date donnée (ISO YYYY-MM-DD) dans le plan en cours.

    Stratégie multi-plans : on parcourt les plans du plus récent au plus ancien
    et on retient le premier dont la fenêtre [première séance ; dernière séance]
    couvre ``date``. Cela privilégie la dernière intention de l'athlète
    (régénération de plan = ancien plan obsolète).
    """
    from domestique_ai.llm.plan_storage import list_plans, load_plan
    from domestique_ai.llm.prescription_storage import get_prescription_for_date

    ctx = ctx or context_from_env()
    try:
        target = dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return {
            "available": False,
            "reason": f"date invalide: {date!r}. Format attendu: YYYY-MM-DD.",
        }

    # Une séance prescrite par le coach prime sur le plan généré ce jour-là.
    prescribed = get_prescription_for_date(target.isoformat(), db_path=ctx.db_path)
    if prescribed is not None:
        return {
            "available": True,
            "source": "prescription",
            "prescribed_by": "coach",
            "planned_workout": prescribed.to_dict(),
        }

    # Tri par id DESC : la précision seconde de `created_at` peut produire des
    # égalités quand on enchaîne plusieurs sauvegardes (cf. load_latest_plan).
    plans = sorted(list_plans(limit=50, db_path=ctx.db_path), key=lambda m: m["id"], reverse=True)
    total_considered = len(plans)
    for meta in plans:
        workouts = load_plan(meta["id"], db_path=ctx.db_path)
        if not workouts:
            continue
        first = dt.date.fromisoformat(workouts[0].date)
        last = dt.date.fromisoformat(workouts[-1].date)
        if not (first <= target <= last):
            continue
        match = next((w for w in workouts if w.date == target.isoformat()), None)
        base = {
            "available": True,
            "source": "plan",
            "plan_id": meta["id"],
            "plan_target_date": meta.get("target_date"),
            "plan_target_event_type": meta.get("target_event_type"),
            "total_plans_considered": total_considered,
        }
        if match is None:
            base["planned_workout"] = None
            base["note"] = "Jour de repos / non programmé dans le plan."
            return base
        base["planned_workout"] = match.to_dict()
        return base

    return {
        "available": False,
        "reason": "Date hors fenêtre des plans connus.",
        "total_plans_considered": total_considered,
    }


def propose_workout_today(
    available_min: int | None = None,
    refresh: bool = False,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Séance optimale pour aujourd'hui (TSB + objectif + plan + contexte).

    Délègue au module ``processing.today``. Retourne soit ``{rest_day: True,
    reason: ...}``, soit ``{rest_day: False, workout: ..., tsb: ...,
    tsb_zone: ..., rationale: ..., signals: ..., source: ...}``. ``source``
    indique d'où vient la décision (cache, llm, fallback, plan).
    """
    from domestique_ai.processing.today import (
        propose_workout_today as _propose_today,
    )

    result = _propose_today(available_min=available_min, refresh=refresh, ctx=ctx)
    # Check du matin (règles, sans LLM) : le prompt système promet que le tool
    # porte `morning_decision` / `morning_reason` — on les fusionne ici pour que
    # le chat puisse citer la décision sans passer par `/api/coach/today`.
    try:
        from domestique_ai.llm.daily_decision import evaluate_daily_decision

        morning = evaluate_daily_decision(ctx=ctx, use_llm=False)
    except Exception:  # noqa: BLE001 — best-effort, ne bloque jamais la séance
        morning = None
    if isinstance(result, dict) and isinstance(morning, dict):
        if morning.get("decision"):
            result.setdefault("morning_decision", morning["decision"])
        if morning.get("reason"):
            result.setdefault("morning_reason", morning["reason"])
    return result


# ---- Schémas JSON pour le LLM ------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_training_load_state",
            "description": "Renvoie l'état courant CTL/ATL/TSB (forme, fatigue, "
            "fraîcheur) + zone interprétative.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_trends",
            "description": "Évolution : CTL/ATL/TSB, mensuels (volume, TSS, "
            "zones), volume hebdo, projection FTP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["3m", "6m", "1y", "all"],
                        "description": "Période (défaut 6m).",
                    },
                    "weeks": {
                        "type": "integer",
                        "description": "Semaines de volume (défaut 12).",
                        "minimum": 1,
                        "maximum": 52,
                    },
                    "include_ftp_projection": {
                        "type": "boolean",
                        "description": "Projection FTP (défaut true).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activities",
            "description": "Activités des N derniers jours (sport, TSS, durée, "
            "distance, dénivelé, FC, zones, RPE, notes) — 10 plus récentes max.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre glissante en jours (défaut 7).",
                        "minimum": 1,
                        "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zone_distribution",
            "description": "Temps cumulé par zone HR (Z1..Z5) sur N jours, en %.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre en jours (défaut 14).",
                        "minimum": 1,
                        "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_objective",
            "description": "Lit l'objectif d'entraînement courant (type, date, "
            "distance, dénivelé, notes).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_profile",
            "description": "Profil athlète : niveau, FTP, W/kg, FC repos/max, "
            "sexe, seuil lactique et zones HR en bpm.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_details",
            "description": "Détail complet d'une activité par son id externe "
            "(+ découplage Pw:HR si streams persistés).",
            "parameters": {
                "type": "object",
                "properties": {
                    "external_id": {
                        "type": "integer",
                        "description": "Identifiant externe de l'activité "
                        "(celui affiché dans l'app).",
                    },
                },
                "required": ["external_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_morning_trends",
            "description": "Métriques matinales : baselines 14 j, dernières "
            "valeurs, série courte, scores Garmin, alertes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre d'historique en jours (défaut 30).",
                        "minimum": 7,
                        "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overtraining_signals",
            "description": "Détecte les signaux de surentraînement à partir "
            "des activités : TSB chronique, monotony et strain "
            "de Foster, saut de volume hebdo. Renvoie alertes "
            "agrégées avec messages explicites.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_mix",
            "description": "Mix par sport (défaut), type de séance (kind) ou "
            "indoor/outdoor : séances, durée, distance, D+, charge. "
            "include_monthly = évolution mensuelle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre en jours (défaut 28).",
                        "minimum": 1,
                        "maximum": 365,
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["sport", "kind", "indoor"],
                        "description": "sport (défaut), kind (zones HR), indoor.",
                    },
                    "include_monthly": {
                        "type": "boolean",
                        "description": "Évolution mensuelle.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nutrition_context",
            "description": "Ancrage factuel pour conseils nutrition : poids, "
            "FTP, W/kg, charge 7 j, séances (durée, plus longue/dure), "
            "température, calories. Pas de journal alimentaire suivi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre d'analyse en jours (défaut 14).",
                        "minimum": 1,
                        "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_training_plan",
            "description": "Génère et persiste le plan déterministe jusqu'à "
            "l'objectif (fallback 4 semaines). Renvoie un summary (TSS hebdo, "
            "semaine pic, séances).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sessions_per_week": {
                        "type": "integer",
                        "description": "Nombre de séances hebdomadaires (défaut 4).",
                        "minimum": 2,
                        "maximum": 7,
                    },
                    "focus": {
                        "type": "string",
                        "description": "Focus pédagogique optionnel "
                        "(ex: 'endurance', 'puissance').",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_planned_workout",
            "description": "Séance prévue à une date (plan actif le plus "
            "récent). `planned_workout=None` si repos, `available=False` "
            "si date hors plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date ISO YYYY-MM-DD de la séance à "
                        "récupérer (typiquement la date d'une "
                        "activité analysée).",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workout_today",
            "description": "Séance optimale du jour (TSB, objectif, plan, "
            "dernière séance, zones, alertes). Renvoie rest_day ou un workout "
            "structuré + rationale + signals — à reformuler pour l'athlète.",
            "parameters": {
                "type": "object",
                "properties": {
                    "available_min": {
                        "type": "integer",
                        "description": "Durée en minutes (override dispo du jour).",
                        "minimum": 20,
                        "maximum": 240,
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Force la régénération (ignore le cache du jour).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workout",
            "description": "Squelette de séance (cyclisme : target_zone ; hors vélo : sport) + durée.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sport": {
                        "type": "string",
                        "enum": ["cyclisme", *_OFFBIKE_TEMPLATES.keys()],
                        "description": "Discipline (défaut cyclisme).",
                    },
                    "target_zone": {
                        "type": "string",
                        "enum": list(HR_ZONE_KEYS),
                        "description": "Zone visée.",
                    },
                    "duration_min": {
                        "type": "integer",
                        "description": "Durée.",
                        "minimum": 15,
                        "maximum": 480,
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(_WORKOUT_TEMPLATES.keys()),
                        "description": "Déduit de target_zone.",
                    },
                },
                "required": ["duration_min"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_week",
            "description": "Rapport de la semaine écoulée (compliance, "
            "récupération, alertes, TSB). Lecture seule — avant un re-plan "
            "ou pour expliquer un ajustement.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_activities",
            "description": "Activités similaires (bucket indoor/outdoor, distance ±5 %, D+ ±10 %).",
            "parameters": {
                "type": "object",
                "properties": {
                    "external_id": {
                        "type": "integer",
                        "description": "Identifiant externe de l'activité de référence.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut 10).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["external_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_best_efforts",
            "description": "Records de puissance : meilleurs efforts 5 s→60 min "
            "(W, W/kg, date) et tendance seuil 20 min par année.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["3m", "6m", "1y", "all"],
                        "description": "Période (défaut 1y).",
                    },
                    "duration_min": {
                        "type": "integer",
                        "description": "Durée ciblée en minutes (optionnel).",
                        "minimum": 1,
                        "maximum": 180,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_climb_stats",
            "description": "Montées (cols/bosses répétés) : passages, meilleur/"
            "moyen temps, VAM, par année. `name` filtre une montée nommée.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom (partiel), ex. « Haut-Koenigsbourg ».",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max de montées (défaut 10).",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Mémorise un fait DURABLE (préférence, contrainte, "
            "objectif, accord, perso). Pas d'état passager (fatigue du jour).",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["preference", "constraint", "goal", "agreement", "personal"],
                        "description": "Catégorie.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Fait autonome (compréhensible hors contexte).",
                    },
                },
                "required": ["category", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_conversations",
            "description": "Recherche sémantique dans les échanges passés "
            "(retrouver un échange précis, vérifier ce qui a été dit).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requête en langage naturel.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre de passages retournés (défaut 5).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_training_load_state": get_training_load_state,
    "get_training_trends": get_training_trends,
    "get_recent_activities": get_recent_activities,
    "get_zone_distribution": get_zone_distribution,
    "get_objective": get_objective,
    "get_profile": get_profile,
    "get_activity_details": get_activity_details,
    "get_morning_trends": get_morning_trends,
    "get_overtraining_signals": get_overtraining_signals,
    "get_activity_mix": get_activity_mix,
    "get_climb_stats": get_climb_stats,
    "get_best_efforts": get_best_efforts,
    "get_nutrition_context": get_nutrition_context,
    "generate_training_plan": generate_training_plan,
    "get_planned_workout": get_planned_workout,
    "propose_workout_today": propose_workout_today,
    "propose_workout": propose_workout,
    "review_week": None,  # type: ignore[dict-item] — assigné plus bas
    "find_similar_activities": None,  # type: ignore[dict-item] — assigné plus bas
}


def review_week(*, ctx: AthleteContext | None = None) -> dict[str, Any]:
    """Rapport de la semaine écoulée (plan vs réalisé + récupération).

    Lecture seule : compliance (fait/partiel/manqué/repos coach, TSS planifié
    vs réalisé), tendances matin (readiness, sommeil, HRV), alertes
    overtraining et TSB courant. Ne re-planifie pas — à combiner avec
    ``generate_training_plan``.
    """
    import datetime as _dt

    from domestique_ai.llm.weekly_review import collect_week_report

    ctx = ctx or context_from_env()
    return collect_week_report(_dt.date.today(), ctx)


TOOLS["review_week"] = review_week


def find_similar_activities(
    external_id: int, limit: int = 10, *, ctx: AthleteContext | None = None
) -> dict[str, Any]:
    """Recherche les activités au profil similaire (même boucle).

    Délègue à ``processing.similar.find_similar_activities``. Wrapper local
    pour pouvoir l'enregistrer dans le dict ``TOOLS`` sans créer de cycle
    d'import au chargement du module.
    """
    from domestique_ai.processing.similar import (
        find_similar_activities as _impl,
    )

    ctx = ctx or context_from_env()
    limit = max(1, min(int(limit), _MAX_SIMILAR_ACTIVITIES))
    return _impl(external_id, limit=limit, db_path=ctx.db_path)


TOOLS["find_similar_activities"] = find_similar_activities


def remember_fact(
    category: str, content: str, *, ctx: AthleteContext | None = None
) -> dict[str, Any]:
    """Mémorise un fait durable sur l'athlète (mémoire persistante).

    Délègue à ``llm.memory.remember_fact``. Dédup par similarité sémantique :
    un fait très proche d'un fait existant met à jour celui-ci.
    """
    from domestique_ai.llm.memory import remember_fact as _impl

    ctx = ctx or context_from_env()
    return _impl(category, content, ctx=ctx)


TOOLS["remember_fact"] = remember_fact


def search_conversations(
    query: str, limit: int = 5, *, ctx: AthleteContext | None = None
) -> dict[str, Any]:
    """Recherche sémantique dans les échanges passés (mémoire conversationnelle)."""
    from domestique_ai.llm.memory import get_relevant_memory

    ctx = ctx or context_from_env()
    hits = get_relevant_memory(query, k=limit, types=("message", "summary", "fact"), ctx=ctx)
    # Tronque le texte renvoyé au LLM : le résultat est réinjecté à chaque
    # itération de la boucle, le passage intégral peut être très long.
    results = [{**hit, "text": (hit.get("text") or "")[:_MAX_SEARCH_CHARS]} for hit in hits]
    return {"query": query, "results": results, "available": bool(results)}


TOOLS["search_conversations"] = search_conversations


def dispatch(
    name: str, arguments: dict[str, Any], ctx: AthleteContext | None = None
) -> dict[str, Any]:
    """Exécute un tool par son nom, scopé sur l'athlète ``ctx``.

    Le ``ctx`` est injecté à chaque tool (en plus des arguments fournis par le
    LLM, qui n'incluent jamais ``ctx``). Tous les tools acceptent donc ``ctx``.
    Fallback ``context_from_env()`` pour les appels hors requête (CLI, tests).
    """
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"Tool inconnu: {name}"}
    ctx = ctx or context_from_env()
    try:
        return fn(**(arguments or {}), ctx=ctx)
    except TypeError as exc:
        return {"error": f"Arguments invalides pour {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Échec de {name}: {exc}"}
