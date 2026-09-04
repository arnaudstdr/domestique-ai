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

import sqlite3
from pathlib import Path

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
                start_lng REAL
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
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_garmin_id "
            "ON activities(garmin_id) WHERE garmin_id IS NOT NULL"
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
                steps INTEGER,
                active_calories INTEGER,
                readiness_score INTEGER,
                sleep_score_computed INTEGER
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
            ("steps", "INTEGER"),
            ("active_calories", "INTEGER"),
            ("readiness_score", "INTEGER"),
            ("sleep_score_computed", "INTEGER"),
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plan_decisions_date ON plan_decisions(date)"
        )
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
