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

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import (
    get_db_path,
    get_ftp,
    get_hr_max,
    get_hr_rest,
    get_lthr_pct,
    get_sex,
)

# Bornes hautes des zones Z1..Z4 en %HRR (Karvonen). Z5 = reste, jusqu'à 1.0.
_HR_ZONE_BOUNDS = (0.60, 0.70, 0.80, 0.90)
HR_ZONE_KEYS = ("z1", "z2", "z3", "z4", "z5")
# Au-delà, on considère qu'il y a eu une pause Strava et on ne comptabilise pas
# le delta dans la zone (sinon une auto-pause de 90 s gonflerait artificiellement
# la zone du dernier sample actif).
_HR_ZONE_PAUSE_GAP_SEC = 5.0


def fetch_activities_from_db(db_path: Path | None = None, *,
                             ctx: AthleteContext | None = None) -> list[dict[str, Any]]:
    """Charge toutes les activités depuis SQLite, triées par date croissante."""
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    if not path.exists():
        return []
    # Import local pour éviter les cycles avec ingestion.strava.
    from domestique_ai.ingestion.strava import init_db
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            "SELECT strava_id, date, duration, avg_heart_rate, max_heart_rate, "
            "avg_power, elevation_gain, distance, training_load, "
            "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time, "
            "sport_type, avg_temp, min_temp, max_temp, map_polyline "
            "FROM activities ORDER BY date ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "strava_id": row[0],
            "date": row[1],
            "duration": row[2],
            "avg_heart_rate": row[3],
            "max_heart_rate": row[4],
            "avg_power": row[5],
            "elevation_gain": row[6],
            "distance": row[7],
            "training_load": row[8],
            "hr_z1_time": row[9],
            "hr_z2_time": row[10],
            "hr_z3_time": row[11],
            "hr_z4_time": row[12],
            "hr_z5_time": row[13],
            "sport_type": row[14],
            "avg_temp": row[15],
            "min_temp": row[16],
            "max_temp": row[17],
            "map_polyline": row[18],
        }
        for row in rows
    ]


def fetch_weight_history(db_path: Path | None = None, *,
                         ctx: AthleteContext | None = None) -> list[dict[str, Any]]:
    """Charge l'historique du poids depuis SQLite, trié par date croissante.

    Retourne une liste de `{"date": "YYYY-MM-DD", "weight": kg}`.
    """
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    if not path.exists():
        return []
    from domestique_ai.ingestion.strava import init_db
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            "SELECT date, weight FROM weight_history ORDER BY date ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [{"date": row[0], "weight": row[1]} for row in rows]


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


def _zone_index(hrr: float) -> int:
    """Retourne l'indice 0..4 de la zone correspondant à un HRR clippé sur [0, 1]."""
    for i, bound in enumerate(_HR_ZONE_BOUNDS):
        if hrr < bound:
            return i
    return 4


def calculate_hr_zones(hr_stream: list[float] | None,
                       time_stream: list[float] | None,
                       hr_rest: float,
                       hr_max: float) -> dict[str, float]:
    """
    Ventile une activité dans 5 zones %HRR (Karvonen) à partir des streams HR.

    hr_stream : fréquence cardiaque seconde par seconde (bpm).
    time_stream : timestamps relatifs en secondes (gère les pauses Strava
                  via la différence entre samples consécutifs).
    Renvoie le temps passé dans chaque zone, en secondes : {"z1": ..., "z5": ...}.
    Les samples HR à 0 ou None sont ignorés (capteur pas encore actif).
    """
    zones = {key: 0.0 for key in HR_ZONE_KEYS}
    if not hr_stream or not time_stream or len(hr_stream) != len(time_stream):
        return zones
    if hr_max <= hr_rest:
        return zones

    hrr_range = hr_max - hr_rest
    n = len(hr_stream)
    for i in range(n):
        hr = hr_stream[i]
        if hr is None or hr <= 0:
            continue
        if i < n - 1:
            dt = float(time_stream[i + 1]) - float(time_stream[i])
            if dt <= 0 or dt > _HR_ZONE_PAUSE_GAP_SEC:
                continue
        else:
            dt = 1.0
        hrr = (float(hr) - hr_rest) / hrr_range
        hrr = max(0.0, min(hrr, 1.0))
        zones[HR_ZONE_KEYS[_zone_index(hrr)]] += dt

    return {key: round(value, 1) for key, value in zones.items()}


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


def recalculate_training_loads(db_path: Path | None = None, *,
                               ctx: AthleteContext | None = None) -> int:
    """
    Recalcule training_load pour toutes les activités selon la config courante.

    Si ``ctx`` est fourni, le profil HR/FTP de ce contexte est utilisé pour le
    calcul ; sinon, on retombe sur la config globale (.env + profil YAML),
    comportement mono-utilisateur historique.

    Retourne le nombre de lignes mises à jour (valeur effectivement modifiée).
    """
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
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
                ftp=ctx.ftp if ctx else None,
                hr_rest=ctx.hr_rest if ctx else None,
                hr_max=ctx.hr_max if ctx else None,
                sex=ctx.sex if ctx else None,
                lthr_pct=ctx.lthr_pct if ctx else None,
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
                          atl_constant: float = 7,
                          end_date: datetime.date | None = None) -> list[dict[str, Any]]:
    """
    Calcule CTL/ATL/TSB jour par jour à partir des activités.

    CTL : moyenne mobile exponentielle du TSS sur ~42 jours (forme à long terme).
    ATL : moyenne mobile exponentielle du TSS sur ~7 jours (fatigue récente).
    TSB : CTL − ATL (positif = frais, négatif = fatigué).

    Si ``end_date`` est fourni et postérieur à la dernière activité, la grille
    est prolongée jusqu'à cette date avec un TSS=0 sur les jours sans activité —
    permettant de suivre la décroissance de l'ATL/CTL pendant une période de repos.
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
    if end_date is not None:
        end_dt = datetime.datetime.combine(end_date, datetime.time())
        if end_dt > end:
            end = end_dt
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
