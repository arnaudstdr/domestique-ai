"""
Module d'analyse des charges d'entraînement pour cyclistes.
Calcule TSS, CTL, ATL, TSB à partir des activités stockées.
"""
import sqlite3
from typing import List, Dict, Any
import datetime
import math
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '../data/strava_activities.db')

# --- Fonctions principales ---

def fetch_activities_from_db(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Récupère toutes les activités depuis la base SQLite.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT date, duration, avg_heart_rate, avg_power, elevation_gain, distance, training_load FROM activities ORDER BY date ASC")
    rows = cursor.fetchall()
    conn.close()
    activities = []
    for row in rows:
        activities.append({
            "date": row[0],
            "duration": row[1],
            "avg_heart_rate": row[2],
            "avg_power": row[3],
            "elevation_gain": row[4],
            "distance": row[5],
            "training_load": row[6],
        })
    return activities

def calculate_tss(duration_sec: int, avg_power: float, ftp: float) -> float:
    """
    Calcule le TSS (Training Stress Score) d'une activité.
    duration_sec : durée en secondes
    avg_power : puissance moyenne (watts)
    ftp : puissance seuil fonctionnelle (watts)
    """
    if not avg_power or not ftp or ftp == 0:
        return 0.0
    duration_hr = duration_sec / 3600
    intensity_factor = avg_power / ftp
    tss = duration_hr * intensity_factor**2 * 100
    return round(tss, 2)

def calculate_ctl_atl_tsb(activities: List[Dict[str, Any]], ctl_constant: float = 42, atl_constant: float = 7) -> List[Dict[str, Any]]:
    """
    Calcule CTL, ATL et TSB pour chaque jour à partir des activités.
    Retourne une liste de dicts avec date, CTL, ATL, TSB.
    """
    # Préparation : regrouper les TSS par date
    tss_by_date = {}
    for act in activities:
        date = act["date"][:10]  # YYYY-MM-DD
        tss = act["training_load"] or 0
        tss_by_date.setdefault(date, 0)
        tss_by_date[date] += tss
    # Générer la liste de dates continues
    if not tss_by_date:
        return []
    dates = sorted(tss_by_date.keys())
    start = datetime.datetime.strptime(dates[0], "%Y-%m-%d")
    end = datetime.datetime.strptime(dates[-1], "%Y-%m-%d")
    all_dates = [(start + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range((end-start).days+1)]
    # Calculs exponentiels
    ctl, atl = 0.0, 0.0
    result = []
    for d in all_dates:
        tss = tss_by_date.get(d, 0)
        ctl = ctl + (tss - ctl) * (1/ctl_constant)
        atl = atl + (tss - atl) * (1/atl_constant)
        tsb = ctl - atl
        result.append({"date": d, "CTL": round(ctl,2), "ATL": round(atl,2), "TSB": round(tsb,2)})
    return result
