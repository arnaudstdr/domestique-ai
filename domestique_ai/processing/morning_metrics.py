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

METRIC_COLUMNS = (
    "hrv_ms",
    "resting_hr",
    "sleep_hours",
    "sleep_score",
    "stress_score",
    "readiness_score",
    "spo2_avg_pct",
    "respiratory_rate_avg_bpm",
    "skin_temp_delta_c",
    "steps",
    "active_calories",
)

# Sens d'alerte par métrique :
# -1 = baisse mauvaise (HRV, sommeil, score sommeil, readiness, SpO2),
# +1 = hausse mauvaise (FC repos, stress, fréquence respiratoire, température).
_ALERT_DIRECTION = {
    "hrv_ms": -1,
    "resting_hr": 1,
    "sleep_hours": -1,
    "sleep_score": -1,
    "stress_score": 1,
    "readiness_score": -1,
    "spo2_avg_pct": -1,
    "respiratory_rate_avg_bpm": 1,
    "skin_temp_delta_c": 1,
    "steps": 0,  # pas d'alerte automatique sur les pas
    "active_calories": 0,  # pas d'alerte automatique sur les calories
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
    spo2_avg_pct: float | None = None,
    respiratory_rate_avg_bpm: float | None = None,
    skin_temp_delta_c: float | None = None,
    sleep_deep_min: int | None = None,
    sleep_rem_min: int | None = None,
    sleep_light_min: int | None = None,
    sleep_awake_min: int | None = None,
    steps: int | None = None,
    active_calories: int | None = None,
    readiness_score: int | None = None,
    sleep_score_computed: int | None = None,
    db_path: Path | None = None,
) -> bool:
    """
    Insère ou remplace une entrée matinale. Idempotent sur la date (PK).

    Retourne True si l'opération a écrit quelque chose, False si tous les
    champs métriques étaient None (pas d'écriture utile).
    """
    metric_values = (
        hrv_ms,
        resting_hr,
        sleep_hours,
        sleep_score,
        stress_score,
        spo2_avg_pct,
        respiratory_rate_avg_bpm,
        skin_temp_delta_c,
        sleep_deep_min,
        sleep_rem_min,
        sleep_light_min,
        sleep_awake_min,
        steps,
        active_calories,
        readiness_score,
        sleep_score_computed,
    )
    if all(v is None for v in (*metric_values, notes)):
        return False
    from domestique_ai.ingestion.strava import init_db

    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO morning_metrics (date, hrv_ms, resting_hr, "
            "sleep_hours, sleep_score, stress_score, notes, spo2_avg_pct, "
            "respiratory_rate_avg_bpm, skin_temp_delta_c, sleep_deep_min, "
            "sleep_rem_min, sleep_light_min, sleep_awake_min, steps, "
            "active_calories, readiness_score, sleep_score_computed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "hrv_ms = excluded.hrv_ms, "
            "resting_hr = excluded.resting_hr, "
            "sleep_hours = excluded.sleep_hours, "
            "sleep_score = excluded.sleep_score, "
            "stress_score = excluded.stress_score, "
            "notes = excluded.notes, "
            "spo2_avg_pct = excluded.spo2_avg_pct, "
            "respiratory_rate_avg_bpm = excluded.respiratory_rate_avg_bpm, "
            "skin_temp_delta_c = excluded.skin_temp_delta_c, "
            "sleep_deep_min = excluded.sleep_deep_min, "
            "sleep_rem_min = excluded.sleep_rem_min, "
            "sleep_light_min = excluded.sleep_light_min, "
            "sleep_awake_min = excluded.sleep_awake_min, "
            "steps = excluded.steps, "
            "active_calories = excluded.active_calories, "
            "readiness_score = excluded.readiness_score, "
            "sleep_score_computed = excluded.sleep_score_computed",
            (
                date,
                hrv_ms,
                resting_hr,
                sleep_hours,
                sleep_score,
                stress_score,
                notes,
                spo2_avg_pct,
                respiratory_rate_avg_bpm,
                skin_temp_delta_c,
                sleep_deep_min,
                sleep_rem_min,
                sleep_light_min,
                sleep_awake_min,
                steps,
                active_calories,
                readiness_score,
                sleep_score_computed,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_morning_entry(
    date: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Charge l'entrée d'une date donnée. None si absente."""
    from domestique_ai.ingestion.strava import init_db

    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT date, hrv_ms, resting_hr, sleep_hours, sleep_score, "
            "stress_score, notes, spo2_avg_pct, respiratory_rate_avg_bpm, "
            "skin_temp_delta_c, sleep_deep_min, sleep_rem_min, sleep_light_min, "
            "sleep_awake_min, steps, active_calories, readiness_score, "
            "sleep_score_computed "
            "FROM morning_metrics WHERE date = ?",
            (date,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def fetch_morning_history(
    days: int | None = None,
    db_path: Path | None = None,
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
            "SELECT date, hrv_ms, resting_hr, sleep_hours, sleep_score, "
            "stress_score, notes, spo2_avg_pct, respiratory_rate_avg_bpm, "
            "skin_temp_delta_c, sleep_deep_min, sleep_rem_min, sleep_light_min, "
            "sleep_awake_min, steps, active_calories, readiness_score, "
            "sleep_score_computed "
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
            alerts.append(
                {
                    "metric": metric,
                    "delta_pct": delta,
                    "baseline": baseline["baseline"],
                    "latest": baseline["latest"],
                    "latest_date": baseline["latest_date"],
                    "severity": "critical" if abs(delta) >= 2 * threshold_pct else "warning",
                }
            )
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
        "spo2_avg_pct": row[7],
        "respiratory_rate_avg_bpm": row[8],
        "skin_temp_delta_c": row[9],
        "sleep_deep_min": row[10],
        "sleep_rem_min": row[11],
        "sleep_light_min": row[12],
        "sleep_awake_min": row[13],
        "steps": row[14],
        "active_calories": row[15],
        "readiness_score": row[16],
        "sleep_score_computed": row[17],
    }


# ---------------------------------------------------------------------------
# Scores calculés (Google Health / Fitbit n'exposent pas ces scores propriétaires)
# ---------------------------------------------------------------------------


def calculate_sleep_score(
    sleep_hours: float | None,
    sleep_deep_min: int | None,
    sleep_rem_min: int | None,
    sleep_light_min: int | None,
    sleep_awake_min: int | None,
) -> int | None:
    """Calcule un score de sommeil maison (0-100) à partir des stades.

    Le score est transparent et décomposé en :
    - 30% durée (cible 7h30)
    - 20% efficacité (temps endormi / temps au lit)
    - 30% qualité (deep + REM proches des ranges idéaux)
    - 20% continuité (temps éveillé faible)

    Retourne ``None`` si aucune donnée de sommeil n'est disponible.
    """
    if sleep_hours is None and sleep_deep_min is None and sleep_rem_min is None:
        return None

    total_sleep_min = 0
    if sleep_hours is not None:
        total_sleep_min = sleep_hours * 60
    elif sleep_light_min is not None or sleep_deep_min is not None or sleep_rem_min is not None:
        total_sleep_min = (sleep_light_min or 0) + (sleep_deep_min or 0) + (sleep_rem_min or 0)
    else:
        return None

    time_in_bed_min = total_sleep_min + (sleep_awake_min or 0)
    if time_in_bed_min <= 0:
        return None

    # Durée : cible 450 min (7h30). Score plein à 450+, pénalité douce en dessous.
    duration_score = min(100.0, (total_sleep_min / 450.0) * 100.0)
    if total_sleep_min < 300:
        duration_score *= 0.7  # pénalité supplémentaire sous 5h

    # Efficacité : objectif 90%+
    efficiency = total_sleep_min / time_in_bed_min
    efficiency_score = min(100.0, efficiency / 0.90 * 100.0)

    # Qualité (deep + REM) : idéaux deep 15-20%, REM 20-25% du temps au lit.
    deep_pct = (sleep_deep_min or 0) / time_in_bed_min
    rem_pct = (sleep_rem_min or 0) / time_in_bed_min
    deep_score = _range_score(deep_pct, 0.15, 0.20)
    rem_score = _range_score(rem_pct, 0.20, 0.25)
    quality_score = (deep_score + rem_score) / 2.0

    # Continuité : awake faible. Objectif < 5% du temps au lit.
    awake_pct = (sleep_awake_min or 0) / time_in_bed_min
    continuity_score = max(0.0, 100.0 - (awake_pct / 0.05) * 50.0)

    score = (
        0.30 * duration_score
        + 0.20 * efficiency_score
        + 0.30 * quality_score
        + 0.20 * continuity_score
    )
    return int(round(max(0.0, min(100.0, score))))


def _range_score(value: float, low: float, high: float) -> float:
    """Score 0-100 : 100 dans [low, high], décroissant à mesure qu'on s'éloigne."""
    if low <= value <= high:
        return 100.0
    if value < low:
        return max(0.0, 100.0 - abs(low - value) / low * 100.0)
    # value > high
    return max(0.0, 100.0 - abs(value - high) / (1.0 - high) * 100.0)


def calculate_readiness_score(
    hrv_ms: float | None,
    resting_hr: float | None,
    sleep_hours: float | None,
    db_path: Path | None = None,
) -> int | None:
    """Calcule un score de readiness maison (0-100) relatif aux baselines.

    Formule :
    - 45% HRV : +10 pts par +10% vs baseline, -10 pts par -10% vs baseline.
    - 30% FC repos : +6 pts par bpm sous la baseline, -6 pts par bpm au-dessus.
    - 25% sommeil : linéaire vers 7h30.

    Retourne ``None`` si HRV ou FC repos manquent (on a besoin d'au moins l'un
    des deux + une baseline).
    """
    if hrv_ms is None and resting_hr is None:
        return None

    hrv_baseline = compute_baselines("hrv_ms", window=14, db_path=db_path)
    rhr_baseline = compute_baselines("resting_hr", window=14, db_path=db_path)

    hrv_component: float | None = None
    rhr_component: float | None = None

    if hrv_ms is not None and hrv_baseline.get("available"):
        baseline = hrv_baseline["baseline"]
        if baseline > 0:
            delta_pct = (hrv_ms - baseline) / baseline * 100.0
            hrv_component = 50.0 + delta_pct
            hrv_component = max(0.0, min(100.0, hrv_component))

    if resting_hr is not None and rhr_baseline.get("available"):
        baseline = rhr_baseline["baseline"]
        if baseline > 0:
            delta_bpm = baseline - resting_hr
            rhr_component = 50.0 + delta_bpm * 6.0
            rhr_component = max(0.0, min(100.0, rhr_component))

    sleep_component = 50.0
    if sleep_hours is not None:
        sleep_component = (sleep_hours / 7.5) * 100.0
        sleep_component = max(0.0, min(100.0, sleep_component))

    weights: list[tuple[float, float]] = []
    if hrv_component is not None:
        weights.append((0.45, hrv_component))
    if rhr_component is not None:
        weights.append((0.30, rhr_component))
    weights.append((0.25, sleep_component))

    if not weights:
        return None

    total_weight = sum(w for w, _ in weights)
    score = sum(w * v for w, v in weights) / total_weight
    return int(round(max(0.0, min(100.0, score))))


def readiness_band(score: int | None) -> str | None:
    """Qualificatif qualitatif du readiness score."""
    if score is None:
        return None
    if score >= 85:
        return "PEAK"
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "BALANCED"
    if score >= 30:
        return "LOW"
    return "VERY_LOW"
