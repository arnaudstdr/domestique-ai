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
import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import get_sync_meta, init_db, set_sync_meta

# Providers de métriques automatiques supportés. ``auto`` privilégie Garmin
# (montre au poignet) et laisse Google Health remplir les jours sans données
# Garmin. Les valeurs correspondent à la colonne ``morning_metrics.source``.
HEALTH_PROVIDER_AUTO = "auto"
HEALTH_PROVIDER_GARMIN = "garmin"
HEALTH_PROVIDER_GOOGLE = "google_health"
HEALTH_PROVIDERS = (HEALTH_PROVIDER_AUTO, HEALTH_PROVIDER_GARMIN, HEALTH_PROVIDER_GOOGLE)

_HEALTH_PROVIDER_KEY = "health_provider"

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
    "weight_kg",
)

# Libellés humains (UI, messages d'alerte, contexte coach) — alignés sur la
# page Santé du front. Source de vérité unique du mapping métrique → libellé.
METRIC_LABELS = {
    "hrv_ms": "HRV",
    "resting_hr": "FC repos",
    "sleep_hours": "Sommeil",
    "sleep_score": "Score sommeil",
    "stress_score": "Stress",
    "readiness_score": "Readiness",
    "spo2_avg_pct": "SpO2 moyen",
    "respiratory_rate_avg_bpm": "Freq. resp.",
    "skin_temp_delta_c": "Δ temp. peau",
    "steps": "Pas",
    "active_calories": "Calories act.",
    "weight_kg": "Poids",
}

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
    "weight_kg": 0,  # variabilité quotidienne normale, pas d'alerte
}

# Seuil par défaut : écart relatif (en %) à partir duquel on lève une alerte.
DEFAULT_ALERT_THRESHOLD_PCT = 10.0


def format_morning_alert(alert: dict[str, Any]) -> str:
    """Formate une alerte de dérive matinale avec un libellé humain.

    `alert` est une entrée de `detect_morning_alerts()`. Repli sur le nom brut
    de la métrique si elle n'a pas de libellé connu.
    """
    metric = alert.get("metric", "")
    label = METRIC_LABELS.get(metric, metric)
    delta = float(alert.get("delta_pct", 0.0))
    arrow = "↓" if delta < 0 else "↑"
    latest = float(alert.get("latest", 0.0))
    return f"{label} {arrow} {delta:+.1f}% vs baseline ({latest:.1f} le {alert.get('latest_date')})"


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
    sleep_stages: list[dict[str, Any]] | None = None,
    steps: int | None = None,
    active_calories: int | None = None,
    readiness_score: int | None = None,
    sleep_score_computed: int | None = None,
    stress_score_computed: int | None = None,
    weight_kg: float | None = None,
    source: str | None = None,
    garmin_sleep_score: int | None = None,
    garmin_readiness_score: int | None = None,
    garmin_body_battery_min: int | None = None,
    garmin_body_battery_max: int | None = None,
    db_path: Path | None = None,
) -> bool:
    """
    Insère ou remplace une entrée matinale. Idempotent sur la date (PK).

    ``source`` trace le provider automatique qui a écrit la ligne
    ("garmin" / "google_health"). Les colonnes bonus ``garmin_*``, ``source``
    et ``sleep_stages_json`` sont préservées quand la mise à jour ne les
    fournit pas (COALESCE) : un sync Google ne doit pas effacer les valeurs
    natives Garmin, et inversement un sync Garmin ne doit pas effacer la
    provenance posée par Google ni l'hypnogramme existant.

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
        stress_score_computed,
        weight_kg,
        garmin_sleep_score,
        garmin_readiness_score,
        garmin_body_battery_min,
        garmin_body_battery_max,
    )
    if all(v is None for v in (*metric_values, notes, sleep_stages)):
        return False
    sleep_stages_json = json.dumps(sleep_stages, ensure_ascii=False) if sleep_stages else None
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO morning_metrics (date, hrv_ms, resting_hr, "
            "sleep_hours, sleep_score, stress_score, notes, spo2_avg_pct, "
            "respiratory_rate_avg_bpm, skin_temp_delta_c, sleep_deep_min, "
            "sleep_rem_min, sleep_light_min, sleep_awake_min, sleep_stages_json, "
            "steps, active_calories, readiness_score, sleep_score_computed, "
            "weight_kg, stress_score_computed, source, garmin_sleep_score, "
            "garmin_readiness_score, garmin_body_battery_min, "
            "garmin_body_battery_max) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
            "sleep_stages_json = COALESCE(excluded.sleep_stages_json, "
            "morning_metrics.sleep_stages_json), "
            "steps = excluded.steps, "
            "active_calories = excluded.active_calories, "
            "readiness_score = excluded.readiness_score, "
            "sleep_score_computed = excluded.sleep_score_computed, "
            "weight_kg = excluded.weight_kg, "
            "stress_score_computed = excluded.stress_score_computed, "
            "source = COALESCE(excluded.source, morning_metrics.source), "
            "garmin_sleep_score = COALESCE(excluded.garmin_sleep_score, "
            "morning_metrics.garmin_sleep_score), "
            "garmin_readiness_score = COALESCE(excluded.garmin_readiness_score, "
            "morning_metrics.garmin_readiness_score), "
            "garmin_body_battery_min = COALESCE(excluded.garmin_body_battery_min, "
            "morning_metrics.garmin_body_battery_min), "
            "garmin_body_battery_max = COALESCE(excluded.garmin_body_battery_max, "
            "morning_metrics.garmin_body_battery_max)",
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
                sleep_stages_json,
                steps,
                active_calories,
                readiness_score,
                sleep_score_computed,
                weight_kg,
                stress_score_computed,
                source,
                garmin_sleep_score,
                garmin_readiness_score,
                garmin_body_battery_min,
                garmin_body_battery_max,
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
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT date, hrv_ms, resting_hr, sleep_hours, sleep_score, "
            "stress_score, notes, spo2_avg_pct, respiratory_rate_avg_bpm, "
            "skin_temp_delta_c, sleep_deep_min, sleep_rem_min, sleep_light_min, "
            "sleep_awake_min, sleep_stages_json, steps, active_calories, "
            "readiness_score, sleep_score_computed, weight_kg, "
            "stress_score_computed, source, garmin_sleep_score, "
            "garmin_readiness_score, garmin_body_battery_min, "
            "garmin_body_battery_max "
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
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT date, hrv_ms, resting_hr, sleep_hours, sleep_score, "
            "stress_score, notes, spo2_avg_pct, respiratory_rate_avg_bpm, "
            "skin_temp_delta_c, sleep_deep_min, sleep_rem_min, sleep_light_min, "
            "sleep_awake_min, sleep_stages_json, steps, active_calories, "
            "readiness_score, sleep_score_computed, weight_kg, "
            "stress_score_computed, source, garmin_sleep_score, "
            "garmin_readiness_score, garmin_body_battery_min, "
            "garmin_body_battery_max "
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


def set_weight(
    date: str,
    weight_kg: float,
    db_path: Path | None = None,
) -> bool:
    """Enregistre le poids d'une date sans toucher aux autres métriques.

    Contrairement à ``save_morning_entry`` (qui écrase toutes les colonnes),
    cet upsert ciblé permet une saisie du poids seule (ex. depuis les réglages)
    sans effacer HRV/sommeil/stress déjà saisis pour la même date.
    """
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO morning_metrics (date, weight_kg) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET weight_kg = excluded.weight_kg",
            (date, float(weight_kg)),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def latest_weight_entry(db_path: Path | None = None) -> tuple[str, float] | None:
    """Dernier poids connu sous forme ``(date, weight_kg)``, ou ``None``."""
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT date, weight_kg FROM morning_metrics "
            "WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return (row[0], float(row[1])) if row is not None else None


def latest_weight(db_path: Path | None = None) -> float | None:
    """Dernier poids connu (colonne ``weight_kg`` non NULL), ou ``None``."""
    entry = latest_weight_entry(db_path=db_path)
    return entry[1] if entry is not None else None


def get_health_provider(db_path: Path | None = None) -> str:
    """Préférence de provider automatique ("auto" par défaut)."""
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    value = get_sync_meta(_HEALTH_PROVIDER_KEY, db_path=path)
    return value if value in HEALTH_PROVIDERS else HEALTH_PROVIDER_AUTO


def set_health_provider(provider: str, db_path: Path | None = None) -> None:
    """Enregistre la préférence de provider automatique."""
    if provider not in HEALTH_PROVIDERS:
        raise ValueError(f"Provider inconnu: {provider!r}")
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    set_sync_meta(_HEALTH_PROVIDER_KEY, provider, db_path=path)


def resolve_health_provider(
    provider: str | None,
    *,
    garmin_connected: bool,
    google_connected: bool,
) -> str | None:
    """Provider effectif qui alimente les métriques automatiques.

    En mode ``auto``, Garmin est prioritaire (montre au poignet) ; Google
    Health ne remplit que les jours sans données Garmin. L'autre provider
    reste utilisable pour combler les trous (sync par date).
    """
    pref = provider if provider in HEALTH_PROVIDERS else HEALTH_PROVIDER_AUTO
    if pref == HEALTH_PROVIDER_GARMIN:
        return HEALTH_PROVIDER_GARMIN if garmin_connected else None
    if pref == HEALTH_PROVIDER_GOOGLE:
        return HEALTH_PROVIDER_GOOGLE if google_connected else None
    if garmin_connected:
        return HEALTH_PROVIDER_GARMIN
    if google_connected:
        return HEALTH_PROVIDER_GOOGLE
    return None


def power_to_weight(power_w: float | None, weight_kg: float | None) -> float | None:
    """Rapport puissance/poids en W/kg, ou ``None`` si une donnée manque."""
    if power_w is None or not weight_kg or weight_kg <= 0:
        return None
    return round(float(power_w) / float(weight_kg), 2)


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
        # Un stress calculé dérive des mêmes signaux (HRV/FC/sommeil) déjà
        # alertés séparément : on n'alerte que sur une valeur saisie à la main.
        if metric == "stress_score":
            entry = fetch_morning_entry(baseline["latest_date"], db_path=db_path)
            if entry is not None and entry.get("stress_score_computed") == 1:
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
        "sleep_stages_json": row[14],
        "steps": row[15],
        "active_calories": row[16],
        "readiness_score": row[17],
        "sleep_score_computed": row[18],
        "weight_kg": row[19],
        "stress_score_computed": row[20],
        "source": row[21],
        "garmin_sleep_score": row[22],
        "garmin_readiness_score": row[23],
        "garmin_body_battery_min": row[24],
        "garmin_body_battery_max": row[25],
    }


# Champs automatiques d'une ligne matinale. ``weight_kg`` en est exclu pour
# ``has_auto_metrics`` : un jour avec seulement un pesage ne doit pas bloquer
# l'autre provider (le poids est conservé par le merge, pas la provenance).
_CORE_AUTO_FIELDS = (
    "hrv_ms",
    "resting_hr",
    "sleep_hours",
    "spo2_avg_pct",
    "respiratory_rate_avg_bpm",
    "skin_temp_delta_c",
    "steps",
    "active_calories",
    "readiness_score",
)

_PROVIDER_AUTO_FIELDS = (
    *_CORE_AUTO_FIELDS,
    "sleep_deep_min",
    "sleep_rem_min",
    "sleep_light_min",
    "sleep_awake_min",
    "weight_kg",
)


def provider_has_data(data: dict[str, Any]) -> bool:
    """True si un fetch provider apporte au moins une métrique automatique."""
    if data.get("sleep_stages"):
        return True
    return any(data.get(field) is not None for field in _PROVIDER_AUTO_FIELDS)


def has_auto_metrics(entry: dict[str, Any] | None) -> bool:
    """True si la ligne porte des métriques automatiques (hors poids)."""
    if not entry:
        return False
    return any(entry.get(field) is not None for field in _CORE_AUTO_FIELDS)


def build_provider_morning_payload(
    existing: dict[str, Any] | None,
    data: dict[str, Any],
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Payload d'écriture commun aux syncs provider (Garmin, Google Health).

    Calcule les scores locaux (sleep, readiness, stress) à partir du dict
    ``data`` (mêmes clés pour tous les providers), préserve un score saisi à la
    main (``sleep_score_computed=0`` / ``stress_score_computed != 1``) et
    complète les métriques absentes du fetch par la valeur existante — un
    provider ne doit jamais effacer une donnée de l'autre avec ``None``.

    Retourne ``None`` si le fetch n'apporte aucune métrique automatique — la
    date doit alors être ignorée par le sync (pas d'écrasement de provenance).
    """
    if not provider_has_data(data):
        return None

    def value(field: str) -> Any:
        new = data.get(field)
        if new is not None:
            return new
        return existing.get(field) if existing else None

    manual_sleep_score = (
        existing is not None
        and existing.get("sleep_score") is not None
        and existing.get("sleep_score_computed") == 0
    )
    if manual_sleep_score:
        sleep_score = existing.get("sleep_score")
        sleep_score_computed = 0
    else:
        sleep_score = calculate_sleep_score(
            data.get("sleep_hours"),
            data.get("sleep_deep_min"),
            data.get("sleep_rem_min"),
            data.get("sleep_light_min"),
            data.get("sleep_awake_min"),
        )
        sleep_score_computed = 1 if sleep_score is not None else None
        if sleep_score is None and existing is not None and existing.get("sleep_score") is not None:
            sleep_score = existing.get("sleep_score")
            sleep_score_computed = existing.get("sleep_score_computed")

    readiness_score = calculate_readiness_score(
        data.get("hrv_ms"),
        data.get("resting_hr"),
        data.get("sleep_hours"),
        db_path=db_path,
    )
    if readiness_score is None and existing is not None:
        readiness_score = existing.get("readiness_score")

    # Une valeur de stress saisie à la main (flag != 1, y compris les lignes
    # historiques au flag NULL) n'est jamais écrasée par le score calculé.
    manual_stress_score = (
        existing is not None
        and existing.get("stress_score") is not None
        and existing.get("stress_score_computed") != 1
    )
    if manual_stress_score:
        stress_score = existing.get("stress_score")
        stress_score_computed = 0
    else:
        stress_score = calculate_stress_score(
            data.get("hrv_ms"),
            data.get("resting_hr"),
            data.get("sleep_hours"),
            sleep_score,
            data.get("respiratory_rate_avg_bpm"),
            data.get("skin_temp_delta_c"),
            data.get("steps"),
            data.get("active_calories"),
            db_path=db_path,
        )
        stress_score_computed = 1 if stress_score is not None else None
        if (
            stress_score is None
            and existing is not None
            and existing.get("stress_score") is not None
        ):
            stress_score = existing.get("stress_score")
            stress_score_computed = existing.get("stress_score_computed")

    return {
        "hrv_ms": value("hrv_ms"),
        "resting_hr": value("resting_hr"),
        "sleep_hours": value("sleep_hours"),
        "sleep_score": sleep_score,
        "sleep_score_computed": sleep_score_computed,
        "spo2_avg_pct": value("spo2_avg_pct"),
        "respiratory_rate_avg_bpm": value("respiratory_rate_avg_bpm"),
        "skin_temp_delta_c": value("skin_temp_delta_c"),
        "sleep_deep_min": value("sleep_deep_min"),
        "sleep_rem_min": value("sleep_rem_min"),
        "sleep_light_min": value("sleep_light_min"),
        "sleep_awake_min": value("sleep_awake_min"),
        "sleep_stages": data.get("sleep_stages"),
        "steps": value("steps"),
        "active_calories": value("active_calories"),
        "readiness_score": readiness_score,
        "stress_score": stress_score,
        "stress_score_computed": stress_score_computed,
        "weight_kg": value("weight_kg"),
        "notes": existing.get("notes") if existing else None,
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


def _autonomic_stress_component(
    hrv_ms: float | None,
    resting_hr: float | None,
    db_path: Path | None,
) -> float | None:
    """Composante autonome (HRV + FC repos vs baseline 14 j), 0-100.

    HRV sous la baseline et/ou FC repos au-dessus → stress plus élevé.
    """
    parts: list[float] = []
    hrv_baseline = compute_baselines("hrv_ms", window=14, db_path=db_path)
    if hrv_ms is not None and hrv_baseline.get("available"):
        baseline = hrv_baseline["baseline"]
        if baseline > 0:
            delta_pct = (hrv_ms - baseline) / baseline * 100.0
            parts.append(max(0.0, min(100.0, 50.0 - delta_pct)))
    rhr_baseline = compute_baselines("resting_hr", window=14, db_path=db_path)
    if resting_hr is not None and rhr_baseline.get("available"):
        baseline = rhr_baseline["baseline"]
        if baseline > 0:
            delta_bpm = resting_hr - baseline
            parts.append(max(0.0, min(100.0, 50.0 + delta_bpm * 6.0)))
    if not parts:
        return None
    return sum(parts) / len(parts)


def _sleep_stress_component(
    sleep_hours: float | None,
    sleep_score: int | None,
) -> float | None:
    """Composante sommeil (0-100) : nuit courte/mauvaise → stress élevé."""
    quality: float | None = None
    if sleep_hours is not None:
        quality = max(0.0, min(100.0, (sleep_hours / 7.5) * 100.0))
    if sleep_score is not None:
        score = max(0.0, min(100.0, float(sleep_score)))
        quality = score if quality is None else (quality + score) / 2.0
    if quality is None:
        return None
    return max(0.0, min(100.0, 100.0 - quality))


def _delta_stress_component(
    value: float | None,
    metric: str,
    factor: float,
    db_path: Path | None,
) -> float | None:
    """Composante dérivée d'un écart relatif vs baseline : 50 + Δ% × facteur."""
    if value is None:
        return None
    baseline = compute_baselines(metric, window=14, db_path=db_path)
    if not baseline.get("available"):
        return None
    base = baseline["baseline"]
    if not base:
        return None
    delta_pct = (value - base) / base * 100.0
    return max(0.0, min(100.0, 50.0 + delta_pct * factor))


def calculate_stress_score(
    hrv_ms: float | None,
    resting_hr: float | None,
    sleep_hours: float | None,
    sleep_score: int | None,
    respiratory_rate_avg_bpm: float | None,
    skin_temp_delta_c: float | None,
    steps: int | None,
    active_calories: int | None,
    db_path: Path | None = None,
) -> int | None:
    """Calcule un score de stress maison (0-100, haut = stress élevé).

    Google Health n'expose ni score de stress ni réponse électrodermale (EDA) :
    ce score est un **proxy** bâti sur les signaux réellement ingérés. Il
    complète le readiness (qui n'utilise que HRV/FC repos/sommeil) en intégrant
    fréquence respiratoire, Δ température cutanée et exertion.

    Composantes (poids, redistribués sur celles disponibles) :
    - 35 % autonome : HRV et FC repos vs baseline 14 j.
    - 25 % sommeil : durée (cible 7h30) + score de sommeil.
    - 15 % fréquence respiratoire vs baseline.
    - 10 % Δ température cutanée.
    - 15 % exertion : pas + calories actives vs baseline.

    Retourne ``None`` si ni signal autonome ni sommeil n'est exploitable.
    """
    components: list[tuple[float, float]] = []

    autonomic = _autonomic_stress_component(hrv_ms, resting_hr, db_path)
    if autonomic is not None:
        components.append((0.35, autonomic))

    sleep = _sleep_stress_component(sleep_hours, sleep_score)
    if sleep is not None:
        components.append((0.25, sleep))

    if autonomic is None and sleep is None:
        return None

    respiratory = _delta_stress_component(
        respiratory_rate_avg_bpm, "respiratory_rate_avg_bpm", 2.0, db_path
    )
    if respiratory is not None:
        components.append((0.15, respiratory))

    if skin_temp_delta_c is not None:
        components.append((0.10, max(0.0, min(100.0, 50.0 + skin_temp_delta_c * 100.0))))

    exertion_parts: list[float] = []
    for value, metric in ((steps, "steps"), (active_calories, "active_calories")):
        comp = _delta_stress_component(value, metric, 0.5, db_path)
        if comp is not None:
            exertion_parts.append(comp)
    if exertion_parts:
        components.append((0.15, sum(exertion_parts) / len(exertion_parts)))

    total_weight = sum(w for w, _ in components)
    if total_weight <= 0:
        return None
    score = sum(w * v for w, v in components) / total_weight
    return int(round(max(0.0, min(100.0, score))))


def stress_band(score: int | None) -> str | None:
    """Qualificatif qualitatif du score de stress."""
    if score is None:
        return None
    if score < 40:
        return "LOW"
    if score <= 70:
        return "MODERATE"
    return "HIGH"
