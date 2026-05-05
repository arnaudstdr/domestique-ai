"""
Analyse des charges d'entraînement.

Calcule TSS (Training Stress Score) à partir de la puissance + FTP,
ou hr-TSS (TRIMP normalisé) à partir de la fréquence cardiaque, puis
les courbes CTL (forme), ATL (fatigue), TSB (fraîcheur) à partir des
activités stockées dans SQLite.
"""

from __future__ import annotations

import datetime
import math
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import (
    get_db_path,
    get_ftp,
    get_hr_max,
    get_hr_rest,
    get_lthr_pct,
    get_sex,
)


def fetch_activities_from_db(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Charge toutes les activités depuis SQLite, triées par date croissante."""
    path = Path(db_path) if db_path else get_db_path()
    if not path.exists():
        return []
    # Import local pour éviter les cycles avec ingestion.strava.
    from domestique_ai.ingestion.strava import init_db
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            "SELECT date, duration, avg_heart_rate, max_heart_rate, avg_power, "
            "elevation_gain, distance, training_load "
            "FROM activities ORDER BY date ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "date": row[0],
            "duration": row[1],
            "avg_heart_rate": row[2],
            "max_heart_rate": row[3],
            "avg_power": row[4],
            "elevation_gain": row[5],
            "distance": row[6],
            "training_load": row[7],
        }
        for row in rows
    ]


def calculate_tss(duration_sec: int, avg_power: float, ftp: float) -> float:
    """
    Calcule le TSS d'une activité à partir de la puissance.

    duration_sec : durée en secondes.
    avg_power : puissance moyenne en watts.
    ftp : Functional Threshold Power en watts.
    """
    if not avg_power or not ftp:
        return 0.0
    duration_hr = duration_sec / 3600
    intensity_factor = avg_power / ftp
    return round(duration_hr * intensity_factor**2 * 100, 2)


def _trimp_coefficients(sex: str) -> tuple[float, float]:
    """Retourne (k1, k2) pour la formule TRIMP exponentielle de Banister."""
    if sex.upper().startswith("F"):
        return 0.86, 1.67
    return 0.64, 1.92


def calculate_trimp(duration_sec: int, avg_hr: float, hr_rest: float,
                    hr_max: float, sex: str = "M") -> float:
    """
    TRIMP exponentiel de Banister — charge d'entraînement basée sur la HR.

    HRR = (avg_hr - hr_rest) / (hr_max - hr_rest)
    TRIMP = duration_min × HRR × k1 × exp(k2 × HRR)
    Coefficients (k1, k2) = (0.64, 1.92) hommes, (0.86, 1.67) femmes.

    Retourne 0.0 si données absentes ou aberrantes.
    """
    if not avg_hr or not hr_max or hr_max <= hr_rest:
        return 0.0
    hrr = (avg_hr - hr_rest) / (hr_max - hr_rest)
    hrr = max(0.0, min(hrr, 1.05))
    duration_min = duration_sec / 60
    k1, k2 = _trimp_coefficients(sex)
    return duration_min * hrr * k1 * math.exp(k2 * hrr)


def calculate_hr_tss(duration_sec: int, avg_hr: float, hr_rest: float,
                     hr_max: float, sex: str = "M",
                     lthr_pct: float = 0.88) -> float:
    """
    TRIMP normalisé en TSS-équivalent : 1h à HRR = lthr_pct ⇒ 100 points.

    Permet d'utiliser les mêmes constantes EMA (42j / 7j) et la même
    interprétation de zones de TSB qu'avec un TSS basé puissance.
    """
    trimp = calculate_trimp(duration_sec, avg_hr, hr_rest, hr_max, sex)
    if trimp <= 0.0:
        return 0.0
    k1, k2 = _trimp_coefficients(sex)
    anchor = 60 * lthr_pct * k1 * math.exp(k2 * lthr_pct)
    return round(trimp / anchor * 100, 2)


def compute_training_load(duration_sec: int,
                          avg_hr: float | None = None,
                          avg_power: float | None = None,
                          ftp: float | None = None,
                          hr_rest: float | None = None,
                          hr_max: float | None = None,
                          sex: str | None = None,
                          lthr_pct: float | None = None) -> float:
    """
    Score de charge d'une activité, en TSS-équivalent.

    Priorité : hr-TSS si HR + HRrepos + HRmax disponibles
              > TSS puissance si avg_power + FTP disponibles
              > 0.0
    Les paramètres absents sont chargés depuis la config (.env).
    """
    duration_sec = int(duration_sec or 0)
    if duration_sec <= 0:
        return 0.0

    hr_rest = hr_rest if hr_rest is not None else get_hr_rest()
    hr_max = hr_max if hr_max is not None else get_hr_max()
    sex = sex or get_sex()
    lthr_pct = lthr_pct if lthr_pct is not None else get_lthr_pct()

    if avg_hr and hr_rest and hr_max and hr_max > hr_rest:
        return calculate_hr_tss(duration_sec, float(avg_hr), float(hr_rest),
                                float(hr_max), sex, lthr_pct)

    ftp = ftp if ftp is not None else get_ftp()
    if avg_power and ftp:
        return calculate_tss(duration_sec, float(avg_power), float(ftp))

    return 0.0


def recalculate_training_loads(db_path: Path | None = None) -> int:
    """
    Recalcule training_load pour toutes les activités selon la config courante.

    Retourne le nombre de lignes mises à jour (valeur effectivement modifiée).
    """
    path = Path(db_path) if db_path else get_db_path()
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, duration, avg_heart_rate, avg_power, training_load "
            "FROM activities"
        ).fetchall()
        updated = 0
        for row_id, duration, avg_hr, avg_power, current_load in rows:
            new_load = compute_training_load(
                duration_sec=duration or 0,
                avg_hr=avg_hr,
                avg_power=avg_power,
            )
            if abs((current_load or 0.0) - new_load) > 1e-6:
                conn.execute(
                    "UPDATE activities SET training_load = ? WHERE id = ?",
                    (new_load, row_id),
                )
                updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


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
