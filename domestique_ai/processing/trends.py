"""
Agrégats longue durée : tendances CTL/ATL/TSB, volumes mensuels, distribution
des zones HR par mois, projection FTP heuristique.

Ce module s'appuie sur ``analyzer.calculate_ctl_atl_tsb`` pour la courbe de
charge et fait ses propres agrégats SQL-libres (en mémoire) à partir des
activités chargées par ``fetch_activities_from_db``. Les calculs sont volontairement
indépendants du routeur FastAPI pour rester facilement testables.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

from domestique_ai.config import get_ftp
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)

Period = Literal["3m", "6m", "1y", "all"]
Resolution = Literal["day", "week", "month"]

_PERIOD_DAYS: dict[Period, int | None] = {
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "all": None,
}

# Résolution choisie en fonction de la période pour garder un nombre de points
# raisonnable côté graphique (~ 30 à 100 points).
_PERIOD_RESOLUTION: dict[Period, Resolution] = {
    "3m": "day",
    "6m": "week",
    "1y": "week",
    "all": "month",
}


def _month_key(date_str: str) -> str:
    """``"2026-05-21..." -> "2026-05"`` (mois ISO)."""
    return date_str[:7]


def _week_key(date_str: str) -> str:
    """Clé ISO ``"YYYY-Www"`` (semaine ISO) à partir d'une date ISO."""
    d = dt.date.fromisoformat(date_str[:10])
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _bucket_load_curve(
    curves: list[dict[str, Any]],
    resolution: Resolution,
) -> list[dict[str, Any]]:
    """Sous-échantillonne la courbe CTL/ATL/TSB selon la résolution.

    On garde la **dernière valeur** de chaque bucket (les EMA sont cumulatifs,
    la dernière valeur de la semaine résume la forme à la fin de cette semaine).
    """
    if resolution == "day" or not curves:
        return curves
    key_fn = _week_key if resolution == "week" else _month_key
    last_by_bucket: dict[str, dict[str, Any]] = {}
    for point in curves:
        last_by_bucket[key_fn(point["date"])] = point
    return list(last_by_bucket.values())


def _filter_by_period(
    activities: list[dict[str, Any]],
    period: Period,
    today: dt.date,
) -> list[dict[str, Any]]:
    """Filtre les activités sur la période demandée (``today`` exclu == None)."""
    days = _PERIOD_DAYS[period]
    if days is None:
        return activities
    cutoff = today - dt.timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    return [a for a in activities if (a.get("date") or "")[:10] >= cutoff_iso]


def _aggregate_monthly(
    activities: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Agrégats mensuels : distance_km, elevation_m, duration_sec, sessions, tss.

    Clé du dict retourné : ``"YYYY-MM"``. Les activités sans date sont ignorées.
    """
    buckets: dict[str, dict[str, float]] = {}
    for act in activities:
        date_str = act.get("date")
        if not date_str:
            continue
        month = _month_key(date_str)
        bucket = buckets.setdefault(
            month,
            {
                "distance_km": 0.0,
                "elevation_m": 0.0,
                "duration_sec": 0.0,
                "sessions": 0.0,
                "tss": 0.0,
            },
        )
        bucket["distance_km"] += float(act.get("distance") or 0) / 1000
        bucket["elevation_m"] += float(act.get("elevation_gain") or 0)
        bucket["duration_sec"] += float(act.get("duration") or 0)
        bucket["sessions"] += 1
        bucket["tss"] += float(act.get("training_load") or 0)
    return buckets


def _aggregate_monthly_zones(
    activities: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Distribution Z1-Z5 par mois, exprimée en pourcentage.

    Une activité dont les zones n'ont pas été ventilées (``hr_zX_time IS NULL``)
    est ignorée pour le calcul de la part — sinon on diluerait artificiellement
    le mois. Les colonnes à 0.0 (calculées mais aucune seconde en zone) sont
    bien comptées.
    """
    raw: dict[str, dict[str, float]] = {}
    for act in activities:
        date_str = act.get("date")
        if not date_str:
            continue
        # On ne ventile que les activités qui ont effectivement été ventilées :
        # si toutes les colonnes sont None, on saute. Si au moins une est posée,
        # on considère les None restantes comme 0.
        zone_values = [act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS]
        if all(v is None for v in zone_values):
            continue
        month = _month_key(date_str)
        bucket = raw.setdefault(month, {key: 0.0 for key in HR_ZONE_KEYS})
        for key, value in zip(HR_ZONE_KEYS, zone_values, strict=True):
            bucket[key] += float(value or 0)

    result: dict[str, dict[str, float]] = {}
    for month, totals in raw.items():
        grand_total = sum(totals.values())
        if grand_total <= 0:
            result[month] = {key: 0.0 for key in HR_ZONE_KEYS}
            continue
        result[month] = {
            key: round(value / grand_total * 100, 1)
            for key, value in totals.items()
        }
    return result


def _months_in_range(start: dt.date, end: dt.date) -> list[str]:
    """Liste continue des clés ``"YYYY-MM"`` du mois de ``start`` au mois de ``end``."""
    months: list[str] = []
    cursor = dt.date(start.year, start.month, 1)
    end_marker = dt.date(end.year, end.month, 1)
    while cursor <= end_marker:
        months.append(f"{cursor.year:04d}-{cursor.month:02d}")
        # Mois suivant.
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return months


def _shift_month_one_year(month_key: str) -> str:
    """``"2026-05" -> "2025-05"`` (utile pour comparer N et N-1)."""
    year = int(month_key[:4])
    return f"{year - 1:04d}-{month_key[5:]}"


def get_trends(
    period: Period = "6m",
    db_path: Path | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Retourne les agrégats longue durée pour la page Tendances.

    Structure :
        {
          "period": "6m",
          "resolution": "week",
          "load_history": [{"date", "ctl", "atl", "tsb"}],
          "monthly": [
              {"month": "2026-05", "distance_km", "elevation_m",
               "duration_sec", "sessions", "tss",
               "distance_km_n1", "tss_n1", ...}
          ],
          "zones_monthly": [
              {"month": "2026-05", "z1": 12.0, "z2": 60.0, ...}
          ],
        }

    ``monthly`` est trié chronologiquement et couvre toute la période demandée
    (mois sans activité = 0). Les champs ``_n1`` sont ``None`` si la donnée
    N-1 n'existe pas pour ce mois.
    """
    if period not in _PERIOD_DAYS:
        raise ValueError(f"period inconnue : {period!r}")
    today = today or dt.date.today()
    resolution = _PERIOD_RESOLUTION[period]

    all_activities = fetch_activities_from_db(db_path)
    period_activities = _filter_by_period(all_activities, period, today)

    # Courbe CTL/ATL/TSB sur toute la base puis on coupe à la période, pour que
    # la valeur du 1er jour de la fenêtre ne reparte pas de zéro.
    full_curves = calculate_ctl_atl_tsb(all_activities, end_date=today)
    days = _PERIOD_DAYS[period]
    if days is not None:
        cutoff_iso = (today - dt.timedelta(days=days)).isoformat()
        windowed_curves = [c for c in full_curves if c["date"] >= cutoff_iso]
    else:
        windowed_curves = full_curves
    bucketed_curves = _bucket_load_curve(windowed_curves, resolution)
    load_history = [
        {
            "date": c["date"],
            "ctl": c["CTL"],
            "atl": c["ATL"],
            "tsb": c["TSB"],
        }
        for c in bucketed_curves
    ]

    # Agrégats mensuels (toujours par mois quelle que soit la résolution
    # graphique : un mois reste l'unité métier naturelle pour le volume).
    monthly_current = _aggregate_monthly(period_activities)
    monthly_all = _aggregate_monthly(all_activities)
    zones_monthly = _aggregate_monthly_zones(period_activities)

    if not period_activities:
        return {
            "period": period,
            "resolution": resolution,
            "load_history": load_history,
            "monthly": [],
        }

    first_date = min(
        dt.date.fromisoformat(a["date"][:10])
        for a in period_activities
        if a.get("date")
    )
    month_keys = _months_in_range(first_date, today)

    monthly_payload: list[dict[str, Any]] = []
    for month in month_keys:
        cur = monthly_current.get(month) or {}
        n1_key = _shift_month_one_year(month)
        n1 = monthly_all.get(n1_key)
        zones = zones_monthly.get(month)
        monthly_payload.append({
            "month": month,
            "distance_km": round(cur.get("distance_km", 0.0), 1),
            "elevation_m": round(cur.get("elevation_m", 0.0), 0),
            "duration_sec": int(cur.get("duration_sec", 0)),
            "sessions": int(cur.get("sessions", 0)),
            "tss": round(cur.get("tss", 0.0), 1),
            "distance_km_n1": round(n1["distance_km"], 1) if n1 else None,
            "tss_n1": round(n1["tss"], 1) if n1 else None,
            "z1_pct": zones["z1"] if zones else None,
            "z2_pct": zones["z2"] if zones else None,
            "z3_pct": zones["z3"] if zones else None,
            "z4_pct": zones["z4"] if zones else None,
            "z5_pct": zones["z5"] if zones else None,
        })

    return {
        "period": period,
        "resolution": resolution,
        "load_history": load_history,
        "monthly": monthly_payload,
    }


# ----------------------------- Projection FTP -------------------------------


def _ctl_value_at(
    curves: list[dict[str, Any]],
    target: dt.date,
) -> float | None:
    """Retourne la CTL à une date donnée (la plus proche antérieure)."""
    if not curves:
        return None
    target_iso = target.isoformat()
    last: float | None = None
    for point in curves:
        if point["date"] <= target_iso:
            last = point["CTL"]
        else:
            break
    return last


def _z4_z5_share(activities: list[dict[str, Any]]) -> float | None:
    """Part Z4+Z5 dans le temps total avec zones ventilées, en pourcentage."""
    z4 = z5 = total = 0.0
    has_data = False
    for act in activities:
        values = [act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS]
        if all(v is None for v in values):
            continue
        has_data = True
        for key, value in zip(HR_ZONE_KEYS, values, strict=True):
            total += float(value or 0)
            if key == "z4":
                z4 += float(value or 0)
            elif key == "z5":
                z5 += float(value or 0)
    if not has_data or total <= 0:
        return None
    return round((z4 + z5) / total * 100, 1)


def get_ftp_projection(
    db_path: Path | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Projection FTP à 4 semaines à partir de la dynamique CTL + part Z4-Z5.

    Heuristique documentée dans la roadmap :
      - ``delta_ctl_28d = CTL(today) - CTL(today - 28d)``.
      - ``gain_pct = clamp(delta_ctl_28d / 5, -5, +5)`` (+1 % FTP par +5 CTL net).
      - Si pas de FTP profil → ``projected_ftp = None``.

    Confiance :
      - ``high`` : ≥ 60 jours d'historique ET part Z4-Z5 entre 4 % et 25 %
        (présence d'un stimulus seuil/VO2max plausible).
      - ``medium`` : ≥ 28 jours d'historique.
      - ``low`` : moins de 28 jours.
    """
    today = today or dt.date.today()
    activities = fetch_activities_from_db(db_path)
    curves = calculate_ctl_atl_tsb(activities, end_date=today)

    ctl_now = _ctl_value_at(curves, today)
    ctl_past = _ctl_value_at(curves, today - dt.timedelta(days=28))

    if ctl_now is None:
        delta_ctl_28d: float | None = None
        gain_pct = 0.0
    else:
        past = ctl_past or 0.0
        delta_ctl_28d = round(ctl_now - past, 2)
        # +1 % FTP pour +5 CTL net, plafonné à ±5 %.
        gain_pct = max(-5.0, min(5.0, delta_ctl_28d / 5.0))

    current_ftp = get_ftp()
    if current_ftp is None or delta_ctl_28d is None:
        projected_ftp: float | None = None
    else:
        projected_ftp = round(float(current_ftp) * (1 + gain_pct / 100), 1)

    # Fenêtre des 28 derniers jours pour la part Z4-Z5.
    cutoff_iso = (today - dt.timedelta(days=28)).isoformat()
    recent = [a for a in activities if (a.get("date") or "")[:10] >= cutoff_iso]
    z4_z5 = _z4_z5_share(recent)

    history_days = len(curves)
    if history_days >= 60 and z4_z5 is not None and 4.0 <= z4_z5 <= 25.0:
        confidence: Literal["low", "medium", "high"] = "high"
    elif history_days >= 28:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "current_ftp": float(current_ftp) if current_ftp is not None else None,
        "projected_ftp": projected_ftp,
        "delta_pct": round(gain_pct, 2),
        "delta_ctl_28d": delta_ctl_28d,
        "ctl_current": round(ctl_now, 2) if ctl_now is not None else None,
        "z4_z5_share_pct": z4_z5,
        "confidence": confidence,
        "history_days": history_days,
    }
