"""
Persistance SQLite des plans d'entraînement générés par le coach (ou l'UI).

Un plan = liste de ``Workout`` (cf. ``processing/plan_builder``). On stocke le
JSON sérialisé entier dans ``training_plans.payload`` ; les colonnes scalaires
servent uniquement à la sélection/aperçu (date cible, nb de semaines).
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.processing.plan_builder import Workout


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    from domestique_ai.ingestion.strava import init_db
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def save_plan(
    plan: list[Workout],
    *,
    target_date: _dt.date | None = None,
    target_event_type: str | None = None,
    sessions_per_week: int | None = None,
    db_path: Path | None = None,
) -> int:
    """Persiste un plan et retourne son ``plan_id``."""
    payload = json.dumps([w.to_dict() for w in plan], ensure_ascii=False)
    weeks = max(1, (len(plan) // max(1, sessions_per_week or 1))) if sessions_per_week else None
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO training_plans "
            "(created_at, target_date, target_event_type, sessions_per_week, weeks, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                target_date.isoformat() if target_date else None,
                target_event_type,
                sessions_per_week,
                weeks,
                payload,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def list_plans(limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Liste les plans connus (le plus récent d'abord)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, target_date, target_event_type, "
            "sessions_per_week, weeks "
            "FROM training_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row[0],
            "created_at": row[1],
            "target_date": row[2],
            "target_event_type": row[3],
            "sessions_per_week": row[4],
            "weeks": row[5],
        }
        for row in rows
    ]


def load_plan(plan_id: int, db_path: Path | None = None) -> list[Workout] | None:
    """Charge un plan par son id."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM training_plans WHERE id = ?", (plan_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = json.loads(row[0])
    return [Workout.from_dict(item) for item in raw]


def load_latest_plan(db_path: Path | None = None) -> tuple[int, list[Workout]] | None:
    """Charge le plan le plus récent (id + workouts), ou None si aucun."""
    # Trier par id DESC (et non created_at) : la précision seconde du timestamp
    # peut produire des égalités quand on enchaîne plusieurs sauvegardes, alors
    # que l'autoincrement garantit l'ordre d'insertion.
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, payload FROM training_plans ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = json.loads(row[1])
    return int(row[0]), [Workout.from_dict(item) for item in raw]


def delete_plan(plan_id: int, db_path: Path | None = None) -> bool:
    """Supprime un plan. Retourne True s'il a été trouvé."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM training_plans WHERE id = ?", (plan_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
