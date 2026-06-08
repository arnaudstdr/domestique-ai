"""
Détection de signaux de surentraînement à partir des activités déjà en base.

Quatre indicateurs auto, calculés sans donnée externe :
- TSB chronique : moyenne TSB sur 7 derniers jours.
- Monotony de Foster : stdev / mean de la charge journalière sur 7 jours.
- Strain de Foster : charge totale 7j × monotony.
- Saut de volume hebdo : (charge W vs W-1) / W-1 en %.

Seuils par défaut issus de la littérature physio sportive (Foster 2001,
Banister, Friel). Ils peuvent être ajustés via les paramètres.
"""

from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path
from typing import Any

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.processing.analyzer import (
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)

TSB_CHRONIC_THRESHOLD = -20.0
MONOTONY_THRESHOLD = 2.0
STRAIN_THRESHOLD = 6000.0
WEEKLY_VOLUME_JUMP_PCT = 30.0


def _daily_loads(
    activities: list[dict[str, Any]],
    days: int,
    *,
    end_date: dt.date | None = None,
) -> tuple[list[float], list[str]]:
    """
    Renvoie la charge agrégée par jour sur les `days` derniers jours
    (par rapport à `end_date`, ou `today()` par défaut), avec 0 pour les jours
    sans activité.

    L'ancrage sur `today()` permet de produire des alertes cohérentes même
    après une période d'arrêt de l'athlète (sinon on raisonnait sur la
    semaine précédant la dernière sortie, potentiellement vieille de
    plusieurs semaines).
    """
    if not activities:
        return [], []
    if end_date is None:
        end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days - 1)
    daily: dict[dt.date, float] = {
        start_date + dt.timedelta(days=i): 0.0 for i in range(days)
    }
    for act in activities:
        d_str = act.get("date")
        if not d_str:
            continue
        d = dt.datetime.fromisoformat(d_str.replace("Z", "+00:00")).date()
        if start_date <= d <= end_date:
            daily[d] += float(act.get("training_load") or 0.0)
    sorted_dates = sorted(daily.keys())
    return [daily[d] for d in sorted_dates], [d.isoformat() for d in sorted_dates]


def compute_chronic_tsb(
    activities: list[dict[str, Any]],
    days: int = 7,
    *,
    end_date: dt.date | None = None,
) -> dict[str, Any]:
    """Moyenne du TSB sur les N derniers jours du calendrier CTL/ATL/TSB."""
    if end_date is None:
        end_date = dt.date.today()
    curves = calculate_ctl_atl_tsb(activities, end_date=end_date)
    if len(curves) < days:
        return {"available": False, "reason": "Pas assez d'historique."}
    window = curves[-days:]
    mean_tsb = statistics.mean(c["TSB"] for c in window)
    return {
        "available": True,
        "days": days,
        "mean_tsb": mean_tsb,
        "alert": mean_tsb < TSB_CHRONIC_THRESHOLD,
        "threshold": TSB_CHRONIC_THRESHOLD,
    }


def compute_monotony_strain(
    activities: list[dict[str, Any]],
    days: int = 7,
    *,
    end_date: dt.date | None = None,
) -> dict[str, Any]:
    """
    Monotony et Strain de Foster sur les N derniers jours (ancrés sur
    `end_date`, ou `today()` par défaut).
    Monotony = mean / stdev (les jours sans activité comptent comme 0).
    Strain = total_load × monotony.

    Si la charge est strictement constante (stdev=0), on plafonne monotony
    à une valeur élevée (10) — cas extrême déclenchant toutes les alertes.
    """
    loads, _ = _daily_loads(activities, days, end_date=end_date)
    if len(loads) < days or sum(loads) == 0:
        return {"available": False, "reason": "Pas assez de charge récente."}
    mean = statistics.mean(loads)
    stdev = statistics.pstdev(loads)
    monotony = 10.0 if stdev == 0 else mean / stdev
    total_load = sum(loads)
    strain = total_load * monotony
    return {
        "available": True,
        "days": days,
        "monotony": monotony,
        "strain": strain,
        "total_load": total_load,
        "alert_monotony": monotony > MONOTONY_THRESHOLD,
        "alert_strain": strain > STRAIN_THRESHOLD,
        "threshold_monotony": MONOTONY_THRESHOLD,
        "threshold_strain": STRAIN_THRESHOLD,
    }


def compute_weekly_jump(
    activities: list[dict[str, Any]],
    *,
    end_date: dt.date | None = None,
) -> dict[str, Any]:
    """
    Comparaison du volume (charge totale) entre la semaine courante (jours
    [end_date - 6 ; end_date]) et la précédente (jours [end_date - 13 ;
    end_date - 7]). Par défaut, `end_date` est `today()`.
    """
    if not activities:
        return {"available": False, "reason": "Pas d'activités."}
    if end_date is None:
        end_date = dt.date.today()
    first_iso = activities[0].get("date")
    if not first_iso:
        return {"available": False, "reason": "Dates manquantes."}
    first_date = dt.datetime.fromisoformat(first_iso.replace("Z", "+00:00")).date()
    if (end_date - first_date).days < 13:
        return {"available": False, "reason": "Pas assez d'historique (<14 j)."}
    loads_14, _ = _daily_loads(activities, 14, end_date=end_date)
    prev_week = sum(loads_14[:7])
    cur_week = sum(loads_14[7:])
    if prev_week == 0:
        return {
            "available": True,
            "current_week_load": cur_week,
            "previous_week_load": 0.0,
            "delta_pct": None,
            "alert": cur_week > 0,
            "threshold_pct": WEEKLY_VOLUME_JUMP_PCT,
            "note": "Reprise après une semaine sans charge.",
        }
    delta_pct = (cur_week - prev_week) / prev_week * 100.0
    return {
        "available": True,
        "current_week_load": cur_week,
        "previous_week_load": prev_week,
        "delta_pct": delta_pct,
        "alert": delta_pct > WEEKLY_VOLUME_JUMP_PCT,
        "threshold_pct": WEEKLY_VOLUME_JUMP_PCT,
    }


def detect_overtraining_signals(
    db_path: Path | None = None,
    *,
    end_date: dt.date | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """
    Calcule les 4 indicateurs et renvoie un rapport agrégé.

    Le bandeau dashboard / le coach LLM peuvent l'exposer tel quel.
    """
    activities = fetch_activities_from_db(db_path=db_path, ctx=ctx)
    chronic = compute_chronic_tsb(activities, end_date=end_date)
    monstrain = compute_monotony_strain(activities, end_date=end_date)
    weekly = compute_weekly_jump(activities, end_date=end_date)

    alerts = []
    if chronic.get("alert"):
        alerts.append({
            "indicator": "tsb_chronic",
            "message": (
                f"TSB moyen {chronic['mean_tsb']:.1f} sur 7 j "
                f"(seuil {TSB_CHRONIC_THRESHOLD}). "
                "Fatigue durable, planifier une semaine de récup."
            ),
        })
    if monstrain.get("alert_monotony"):
        alerts.append({
            "indicator": "monotony",
            "message": (
                f"Monotony {monstrain['monotony']:.2f} > {MONOTONY_THRESHOLD}. "
                "Manque de variabilité dans la charge — alterner intensités."
            ),
        })
    if monstrain.get("alert_strain"):
        alerts.append({
            "indicator": "strain",
            "message": (
                f"Strain {monstrain['strain']:.0f} > {STRAIN_THRESHOLD}. "
                "Charge cumulée élevée, réduire le volume cette semaine."
            ),
        })
    if weekly.get("alert"):
        delta = weekly.get("delta_pct")
        if delta is not None:
            alerts.append({
                "indicator": "weekly_jump",
                "message": (
                    f"Volume hebdo +{delta:.1f}% vs semaine précédente "
                    f"(seuil +{WEEKLY_VOLUME_JUMP_PCT}%). Risque de blessure."
                ),
            })
    return {
        "available": True,
        "tsb_chronic": chronic,
        "monotony_strain": monstrain,
        "weekly_jump": weekly,
        "alerts": alerts,
        "alert_count": len(alerts),
    }
