"""
Persistance SQLite des séances prescrites par un coach (roster).

Une prescription = une séance ponctuelle assignée à une date précise dans
l'espace de l'athlète. On stocke le JSON ``Workout.to_dict()`` dans
``prescriptions.payload`` ; les colonnes scalaires (``date``, ``created_by``)
servent à la sélection et à l'attribution.

Une prescription **prime** sur le plan généré pour la même date (cf.
``processing/today.py`` et ``llm/tools.py``).
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.plan_builder import (
    _TARGET_ZONE,
    _TSS_PER_MIN,
    Workout,
    _name_for,
    _structure_for,
)

_VALID_KINDS = ("recovery", "endurance", "tempo", "intervals")
_MIN_DURATION = 20


class PrescriptionError(ValueError):
    """Input de prescription invalide (kind inconnu, durée trop courte, date KO)."""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def workout_from_choice(date: str, kind: str, duration_min: int, notes: str = "") -> Workout:
    """Reconstruit un ``Workout`` complet depuis les choix de haut niveau du coach.

    Réutilise les helpers du builder déterministe (``_structure_for``,
    ``_TARGET_ZONE``, ``_TSS_PER_MIN``, ``_name_for``) — même logique que
    ``llm/plan_generator._expand_to_workout``. Le coach ne peut donc pas
    produire de structure aberrante.
    """
    if kind not in _VALID_KINDS:
        raise PrescriptionError(f"kind invalide: {kind!r}. Attendu l'un de {_VALID_KINDS}.")
    try:
        _dt.date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise PrescriptionError(f"date invalide: {date!r}. Format attendu YYYY-MM-DD.") from exc
    duration = max(_MIN_DURATION, int(duration_min))
    return Workout(
        date=date,
        name=_name_for(kind, duration, 0, False, False),
        sport="cycling",
        kind=kind,
        duration_min=duration,
        target_zone=_TARGET_ZONE[kind],
        structure=_structure_for(kind, duration),
        estimated_tss=round(_TSS_PER_MIN[kind] * duration, 1),
        notes=notes or "",
    )


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "date": row[1],
        "created_at": row[2],
        "created_by": row[3],
        "workout": json.loads(row[4]),
    }


def save_prescription(
    date: str,
    kind: str,
    duration_min: int,
    notes: str = "",
    *,
    created_by: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Construit et persiste une prescription. Retourne la ligne créée."""
    workout = workout_from_choice(date, kind, duration_min, notes)
    created_at = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    payload = json.dumps(workout.to_dict(), ensure_ascii=False)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO prescriptions (date, created_at, created_by, payload) VALUES (?, ?, ?, ?)",
            (date, created_at, created_by, payload),
        )
        conn.commit()
        pid = int(cursor.lastrowid)
    finally:
        conn.close()
    return {
        "id": pid,
        "date": date,
        "created_at": created_at,
        "created_by": created_by,
        "workout": workout.to_dict(),
    }


def list_prescriptions(
    db_path: Path | None = None, *, since: str | None = None
) -> list[dict[str, Any]]:
    """Liste les prescriptions, par date croissante (optionnellement depuis ``since``)."""
    conn = _connect(db_path)
    try:
        if since:
            rows = conn.execute(
                "SELECT id, date, created_at, created_by, payload FROM prescriptions "
                "WHERE date >= ? ORDER BY date ASC, id ASC",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, date, created_at, created_by, payload FROM prescriptions "
                "ORDER BY date ASC, id ASC"
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def get_prescription_for_date(date: str, db_path: Path | None = None) -> Workout | None:
    """Séance prescrite pour ``date`` (la plus récente si plusieurs), ou None."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM prescriptions WHERE date = ? ORDER BY id DESC LIMIT 1",
            (date,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return Workout.from_dict(json.loads(row[0]))


def delete_prescription(pid: int, db_path: Path | None = None) -> bool:
    """Supprime une prescription. Retourne True si trouvée."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM prescriptions WHERE id = ?", (pid,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
