"""
Analyse des charges d'entraînement.

Calcule TSS (Training Stress Score), CTL (Chronic Training Load),
ATL (Acute Training Load), TSB (Training Stress Balance) à partir
des activités stockées dans SQLite.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path


def fetch_activities_from_db(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Charge toutes les activités depuis SQLite, triées par date croissante."""
    path = Path(db_path) if db_path else get_db_path()
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            "SELECT date, duration, avg_heart_rate, avg_power, elevation_gain, "
            "distance, training_load FROM activities ORDER BY date ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "date": row[0],
            "duration": row[1],
            "avg_heart_rate": row[2],
            "avg_power": row[3],
            "elevation_gain": row[4],
            "distance": row[5],
            "training_load": row[6],
        }
        for row in rows
    ]


def calculate_tss(duration_sec: int, avg_power: float, ftp: float) -> float:
    """
    Calcule le TSS d'une activité.

    duration_sec : durée en secondes.
    avg_power : puissance moyenne en watts.
    ftp : Functional Threshold Power en watts.
    """
    if not avg_power or not ftp:
        return 0.0
    duration_hr = duration_sec / 3600
    intensity_factor = avg_power / ftp
    return round(duration_hr * intensity_factor**2 * 100, 2)


def calculate_ctl_atl_tsb(activities: list[dict[str, Any]],
                          ctl_constant: float = 42,
                          atl_constant: float = 7) -> list[dict[str, Any]]:
    """
    Calcule CTL/ATL/TSB jour par jour à partir des activités.

    CTL : moyenne mobile exponentielle du TSS sur ~42 jours (forme à long terme).
    ATL : moyenne mobile exponentielle du TSS sur ~7 jours (fatigue récente).
    TSB : CTL − ATL (positif = frais, négatif = fatigué).
    """
    tss_by_date: dict[str, float] = {}
    for act in activities:
        if not act.get("date"):
            continue
        date_key = act["date"][:10]
        tss_by_date[date_key] = tss_by_date.get(date_key, 0) + (act.get("training_load") or 0)

    if not tss_by_date:
        return []

    dates = sorted(tss_by_date.keys())
    start = datetime.datetime.strptime(dates[0], "%Y-%m-%d")
    end = datetime.datetime.strptime(dates[-1], "%Y-%m-%d")
    all_dates = [
        (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range((end - start).days + 1)
    ]

    ctl, atl = 0.0, 0.0
    result: list[dict[str, Any]] = []
    for d in all_dates:
        tss = tss_by_date.get(d, 0)
        ctl = ctl + (tss - ctl) * (1 / ctl_constant)
        atl = atl + (tss - atl) * (1 / atl_constant)
        result.append({
            "date": d,
            "CTL": round(ctl, 2),
            "ATL": round(atl, 2),
            "TSB": round(ctl - atl, 2),
        })
    return result
