"""Persistance SQLite source-agnostique : schéma + helpers de migration.

Le module centralise ``init_db`` (schéma complet : activités, conversations,
plans, prescriptions, métriques matinales…) et les migrations douces. Il ne
dépend d'aucune source d'ingestion (Strava, Garmin…) ni du processing —
les modules aval (analyzer, LLM, routers) peuvent l'importer au top-level
sans risque de cycle.

Conventions DB ``activities`` :
- ``strava_id`` : identifiant externe des activités historiques (ingestion
  Strava supprimée en 09/2026). Les lignes récentes ont ``strava_id`` NULL.
- ``garmin_id`` : identifiant Garmin Connect (source d'ingestion courante),
  index unique partiel.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_db_path


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Ajoute une colonne si absente. Migration douce SQLite."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: Path | None = None, *, ctx: AthleteContext | None = None) -> None:
    """Crée la table `activities` et applique les migrations idempotentes."""
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strava_id INTEGER UNIQUE,
                date TEXT,
                duration INTEGER,
                avg_heart_rate REAL,
                max_heart_rate REAL,
                avg_power REAL,
                elevation_gain REAL,
                distance REAL,
                training_load REAL,
                hr_z1_time REAL,
                hr_z2_time REAL,
                hr_z3_time REAL,
                hr_z4_time REAL,
                hr_z5_time REAL,
                sport_type TEXT,
                avg_temp REAL,
                min_temp REAL,
                max_temp REAL,
                map_polyline TEXT,
                name TEXT,
                calories REAL,
                max_power REAL,
                cadence_avg REAL,
                cadence_max REAL,
                speed_avg REAL,
                speed_max REAL,
                elevation_loss REAL,
                start_lat REAL,
                start_lng REAL,
                source TEXT,
                source_uid TEXT
            )
        """)
        _ensure_column(conn, "activities", "max_heart_rate", "REAL")
        for zone in ("hr_z1_time", "hr_z2_time", "hr_z3_time", "hr_z4_time", "hr_z5_time"):
            _ensure_column(conn, "activities", zone, "REAL")
        _ensure_column(conn, "activities", "sport_type", "TEXT")
        for temp_col in ("avg_temp", "min_temp", "max_temp"):
            _ensure_column(conn, "activities", temp_col, "REAL")
        _ensure_column(conn, "activities", "map_polyline", "TEXT")
        # Source Garmin Connect (source d'ingestion courante) : les lignes
        # Garmin ont strava_id NULL et garmin_id renseigné.
        _ensure_column(conn, "activities", "garmin_id", "INTEGER")
        # Champs enrichis (payload liste Garmin 09/2026) : parsing défensif —
        # absents selon device/sport, donc tous nullable.
        for col, ddl in (
            ("name", "TEXT"),
            ("calories", "REAL"),
            ("max_power", "REAL"),
            ("cadence_avg", "REAL"),
            ("cadence_max", "REAL"),
            ("speed_avg", "REAL"),
            ("speed_max", "REAL"),
            ("elevation_loss", "REAL"),
            ("start_lat", "REAL"),
            ("start_lng", "REAL"),
        ):
            _ensure_column(conn, "activities", col, ddl)
        # Source d'ingestion explicite : "garmin" | "strava" | "manual" | "tcx".
        # ``source_uid`` : clé de dédup interne (sha1 du fichier importé) — NULL
        # pour les saisies manuelles (deux séances identiques peuvent être
        # légitimes). Les lignes historiques (source NULL) sont rétro-remplies.
        _ensure_column(conn, "activities", "source", "TEXT")
        _ensure_column(conn, "activities", "source_uid", "TEXT")
        conn.execute(
            "UPDATE activities SET source = CASE "
            "WHEN strava_id IS NOT NULL THEN 'strava' "
            "WHEN garmin_id IS NOT NULL THEN 'garmin' END "
            "WHERE source IS NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_garmin_id "
            "ON activities(garmin_id) WHERE garmin_id IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_source_uid "
            "ON activities(source_uid) WHERE source_uid IS NOT NULL"
        )
        # Normalisation des sport_type hérités d'un mappage Garmin antérieur
        # (typeKey "road_biking" non mappé → fallback "RoadBiking"). Idempotent,
        # no-op une fois la base corrigée — pas de flag sync_meta nécessaire.
        conn.execute("UPDATE activities SET sport_type = 'Ride' WHERE sport_type = 'RoadBiking'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_titles (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_history (
                date TEXT PRIMARY KEY,
                weight REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS morning_metrics (
                date TEXT PRIMARY KEY,
                hrv_ms REAL,
                resting_hr REAL,
                sleep_hours REAL,
                sleep_score INTEGER,
                stress_score INTEGER,
                notes TEXT,
                spo2_avg_pct REAL,
                respiratory_rate_avg_bpm REAL,
                skin_temp_delta_c REAL,
                sleep_deep_min INTEGER,
                sleep_rem_min INTEGER,
                sleep_light_min INTEGER,
                sleep_awake_min INTEGER,
                sleep_stages_json TEXT,
                steps INTEGER,
                active_calories INTEGER,
                readiness_score INTEGER,
                sleep_score_computed INTEGER,
                weight_kg REAL
            )
        """)
        for col, ddl in (
            ("spo2_avg_pct", "REAL"),
            ("respiratory_rate_avg_bpm", "REAL"),
            ("skin_temp_delta_c", "REAL"),
            ("sleep_deep_min", "INTEGER"),
            ("sleep_rem_min", "INTEGER"),
            ("sleep_light_min", "INTEGER"),
            ("sleep_awake_min", "INTEGER"),
            ("sleep_stages_json", "TEXT"),
            ("steps", "INTEGER"),
            ("active_calories", "INTEGER"),
            ("readiness_score", "INTEGER"),
            ("sleep_score_computed", "INTEGER"),
            ("weight_kg", "REAL"),
        ):
            _ensure_column(conn, "morning_metrics", col, ddl)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                target_date TEXT,
                target_event_type TEXT,
                sessions_per_week INTEGER,
                weeks INTEGER,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                parent_plan_id INTEGER,
                start_date TEXT,
                adapt_reason TEXT
            )
        """)
        for col, ddl in (
            ("status", "TEXT DEFAULT 'active'"),
            ("parent_plan_id", "INTEGER"),
            ("start_date", "TEXT"),
            ("adapt_reason", "TEXT"),
        ):
            _ensure_column(conn, "training_plans", col, ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_training_plans_created "
            "ON training_plans(created_at DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER,
                date TEXT NOT NULL,
                decision TEXT NOT NULL,
                workout_payload TEXT,
                reason TEXT,
                decided_by TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (plan_id, date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_decisions_date ON plan_decisions(date)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                payload TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prescriptions_date ON prescriptions(date)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS today_suggestions (
                date TEXT NOT NULL,
                objective_hash TEXT NOT NULL,
                tsb_rounded REAL NOT NULL,
                payload TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (date, objective_hash, tsb_rounded)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Streams persistés uniquement pour les activités importées (TCX) : les
        # activités Garmin les récupèrent en live (cache 1 h) et n'ont pas de
        # ligne ici. ``activity_id`` référence ``activities.id`` (pas l'id
        # externe, qui peut être un garmin_id/strava_id ou l'id local).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_streams (
                activity_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def summarize_temp_stream(
    temp_stream: list[float] | None,
) -> tuple[float, float, float] | None:
    """Réduit un stream de température en triplet ``(avg, min, max)`` en °C.

    Retourne ``None`` si le stream est vide ou ne contient que des valeurs
    aberrantes. Les samples ``None`` ou hors plage plausible
    (``-50 °C < t < 60 °C``) sont ignorés — on garde les ``0.0`` qui sont
    parfaitement légitimes (météo hivernale).
    """
    if not temp_stream:
        return None
    clean = [float(t) for t in temp_stream if t is not None and -50 < float(t) < 60]
    if not clean:
        return None
    avg = round(sum(clean) / len(clean), 1)
    return avg, round(min(clean), 1), round(max(clean), 1)


# Colonnes autorisées à l'écriture via ``insert_activity`` — volontairement
# explicite : tout ajout de colonne doit être déclaré ici en plus du schéma.
_ACTIVITY_COLUMNS: tuple[str, ...] = (
    "strava_id",
    "garmin_id",
    "date",
    "duration",
    "avg_heart_rate",
    "max_heart_rate",
    "avg_power",
    "elevation_gain",
    "distance",
    "training_load",
    "hr_z1_time",
    "hr_z2_time",
    "hr_z3_time",
    "hr_z4_time",
    "hr_z5_time",
    "sport_type",
    "avg_temp",
    "min_temp",
    "max_temp",
    "map_polyline",
    "name",
    "calories",
    "max_power",
    "cadence_avg",
    "cadence_max",
    "speed_avg",
    "speed_max",
    "elevation_loss",
    "start_lat",
    "start_lng",
    "source",
    "source_uid",
)


def _resolve_path(db_path: Path | None, ctx: AthleteContext | None) -> Path:
    return Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())


def insert_activity(
    record: dict[str, Any],
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> int:
    """Insère une activité à partir d'un dict de colonnes explicites.

    Helper source-agnostique utilisé par l'ajout manuel et l'import TCX.
    Les clés absentes valent ``NULL``. Retourne le ``rowid`` inséré — c'est cet
    id local qui sert d'``external_id`` pour les activités sans id Garmin/Strava.
    """
    path = _resolve_path(db_path, ctx)
    init_db(path)
    columns = [col for col in _ACTIVITY_COLUMNS if col in record]
    values = [record[col] for col in columns]
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            f"INSERT INTO activities ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def store_activity_streams(
    activity_id: int,
    payload: dict[str, Any],
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> None:
    """Persiste (upsert) les streams JSON d'une activité importée (TCX)."""
    path = _resolve_path(db_path, ctx)
    init_db(path)
    created_at = dt.datetime.now(dt.UTC).isoformat()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO activity_streams (activity_id, payload, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(activity_id) DO UPDATE SET "
            "payload = excluded.payload, created_at = excluded.created_at",
            (int(activity_id), json.dumps(payload), created_at),
        )
        conn.commit()
    finally:
        conn.close()


def load_activity_streams(
    activity_id: int,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Retourne les streams persistés d'une activité, ou ``None`` si absents."""
    path = _resolve_path(db_path, ctx)
    if not path.exists():
        return None
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT payload FROM activity_streams WHERE activity_id = ?",
            (int(activity_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def activity_id_for_source_uid(
    source_uid: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> int | None:
    """Id local de l'activité portant ``source_uid`` (dédup import), ou ``None``."""
    path = _resolve_path(db_path, ctx)
    if not path.exists():
        return None
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT id FROM activities WHERE source_uid = ?", (source_uid,)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else None


def delete_activity(
    activity_id: int,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> bool:
    """Supprime une activité (et ses streams) par id local. ``True`` si supprimée."""
    path = _resolve_path(db_path, ctx)
    if not path.exists():
        return False
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM activity_streams WHERE activity_id = ?", (int(activity_id),))
        cursor = conn.execute("DELETE FROM activities WHERE id = ?", (int(activity_id),))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_sync_meta(key: str, db_path: Path | None = None) -> str | None:
    """Lit une valeur de la table ``sync_meta`` (flags de maintenance one-off)."""
    path = Path(db_path) if db_path else get_db_path()
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def set_sync_meta(key: str, value: str, db_path: Path | None = None) -> None:
    """Écrit (upsert) une valeur dans ``sync_meta``."""
    path = Path(db_path) if db_path else get_db_path()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
