"""
Métriques matinales (HRV, FC repos, sommeil, stress) saisies manuellement
depuis l'app Zepp / un bracelet Amazfit.

Persistance dans la table `morning_metrics` (clé = date YYYY-MM-DD).
Tous les champs sauf `date` sont optionnels. Une saisie partielle est valide
(typiquement HRV + FC repos un jour, sommeil + stress le lendemain).

Les baselines sont des moyennes mobiles sur une fenêtre glissante. Elles
servent de référence pour détecter une dérive (HRV en chute, FC repos en
hausse, etc.) — signaux classiques de surentraînement.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path

METRIC_COLUMNS = ("hrv_ms", "resting_hr", "sleep_hours",
                  "sleep_score", "stress_score")

# Sens d'alerte par métrique : -1 = baisse mauvaise (HRV, sommeil),
# +1 = hausse mauvaise (FC repos, stress).
_ALERT_DIRECTION = {
    "hrv_ms": -1,
    "resting_hr": 1,
    "sleep_hours": -1,
    "sleep_score": -1,
    "stress_score": 1,
}

# Seuil par défaut : écart relatif (en %) à partir duquel on lève une alerte.
DEFAULT_ALERT_THRESHOLD_PCT = 10.0


def save_morning_entry(
    date: str,
    *,
    hrv_ms: float | None = None,
    resting_hr: float | None = None,
    sleep_hours: float | None = None,
    sleep_score: int | None = None,
    stress_score: int | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """
    Insère ou remplace une entrée matinale. Idempotent sur la date (PK).

    Retourne True si l'opération a écrit quelque chose, False si tous les
    champs métriques étaient None (pas d'écriture utile).
    """
    if all(v is None for v in (hrv_ms, resting_hr, sleep_hours,
                               sleep_score, stress_score, notes)):
        return False
    from domestique_ai.ingestion.strava import init_db
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO morning_metrics (date, hrv_ms, resting_hr, "
            "sleep_hours, sleep_score, stress_score, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "hrv_ms = excluded.hrv_ms, "
            "resting_hr = excluded.resting_hr, "
            "sleep_hours = excluded.sleep_hours, "
            "sleep_score = excluded.sleep_score, "
            "stress_score = excluded.stress_score, "
            "notes = excluded.notes",
            (date, hrv_ms, resting_hr, sleep_hours,
             sleep_score, stress_score, notes),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_morning_entry(
    date: str, db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Charge l'entrée d'une date donnée. None si absente."""
    from domestique_ai.ingestion.strava import init_db
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT date, hrv_ms, resting_hr, sleep_hours, "
            "sleep_score, stress_score, notes "
            "FROM morning_metrics WHERE date = ?",
            (date,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def fetch_morning_history(
    days: int | None = None, db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Charge l'historique trié par date croissante. Si `days` est fourni,
    ne renvoie que les entrées dans la fenêtre glissante (par rapport à
    la dernière entrée connue).
    """
    from domestique_ai.ingestion.strava import init_db
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT date, hrv_ms, resting_hr, sleep_hours, "
            "sleep_score, stress_score, notes "
            "FROM morning_metrics ORDER BY date ASC"
        ).fetchall()
    finally:
        conn.close()
    entries = [_row_to_dict(row) for row in rows]
    if days is None or not entries:
        return entries
    last_date = dt.date.fromisoformat(entries[-1]["date"])
    cutoff = last_date - dt.timedelta(days=days)
    return [e for e in entries if dt.date.fromisoformat(e["date"]) > cutoff]


def compute_baselines(
    metric: str,
    window: int = 14,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """
    Calcule la baseline (moyenne mobile) d'une métrique sur la fenêtre
    glissante demandée, et l'écart relatif de la dernière valeur connue.

    metric : nom de la colonne (hrv_ms, resting_hr, sleep_hours, …).
    Retourne :
      - available: bool
      - reason: str (si indisponible)
      - baseline: float (moyenne sur les N entrées précédentes)
      - latest: float (dernière valeur)
      - latest_date: str
      - delta_pct: float (écart % de latest vs baseline)
      - sample_size: int (nb de points utilisés pour la baseline)
    """
    if metric not in METRIC_COLUMNS:
        return {"available": False, "reason": f"Métrique inconnue: {metric!r}"}
    history = fetch_morning_history(db_path=db_path)
    values = [(e["date"], e[metric]) for e in history if e[metric] is not None]
    if len(values) < 2:
        return {
            "available": False,
            "reason": "Pas assez d'historique (au moins 2 entrées requises).",
        }
    latest_date, latest = values[-1]
    prior = [v for _, v in values[:-1]][-window:]
    if not prior:
        return {"available": False, "reason": "Pas de baseline disponible."}
    baseline = statistics.mean(prior)
    delta_pct = 0.0 if baseline == 0 else (latest - baseline) / baseline * 100.0
    return {
        "available": True,
        "metric": metric,
        "baseline": baseline,
        "latest": latest,
        "latest_date": latest_date,
        "delta_pct": delta_pct,
        "sample_size": len(prior),
    }


def detect_morning_alerts(
    threshold_pct: float = DEFAULT_ALERT_THRESHOLD_PCT,
    window: int = 14,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Renvoie la liste des métriques en dérive vs baseline.

    Une métrique est en alerte si l'écart relatif dépasse `threshold_pct`
    dans le sens défavorable (HRV ↓, FC repos ↑, etc.).
    """
    alerts = []
    for metric in METRIC_COLUMNS:
        baseline = compute_baselines(metric, window=window, db_path=db_path)
        if not baseline.get("available"):
            continue
        direction = _ALERT_DIRECTION[metric]
        delta = baseline["delta_pct"]
        # delta * direction > 0 = écart dans le sens défavorable
        if delta * direction >= threshold_pct:
            alerts.append({
                "metric": metric,
                "delta_pct": delta,
                "baseline": baseline["baseline"],
                "latest": baseline["latest"],
                "latest_date": baseline["latest_date"],
                "severity": "critical" if abs(delta) >= 2 * threshold_pct
                            else "warning",
            })
    return alerts


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "date": row[0],
        "hrv_ms": row[1],
        "resting_hr": row[2],
        "sleep_hours": row[3],
        "sleep_score": row[4],
        "stress_score": row[5],
        "notes": row[6],
    }
