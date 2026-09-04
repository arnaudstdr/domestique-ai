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

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.plan_builder import Workout, build_training_plan


class PlanGenerationError(RuntimeError):
    """Erreur fonctionnelle de génération de plan (input invalide, etc.)."""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
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
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
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
        row = conn.execute("SELECT payload FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
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
        cursor = conn.execute("DELETE FROM training_plans WHERE id = ?", (plan_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def build_and_save_plan(
    sessions_per_week: int = 4,
    focus: str | None = None,
    *,
    ctx: AthleteContext | None = None,
) -> tuple[int, list[Workout], dict[str, Any]]:
    """Génère un plan d'entraînement et le persiste.

    Lit l'objectif (`data/objective.yaml`), l'availability
    (`data/availability.yaml`), calcule le CTL courant depuis la DB, puis
    appelle ``build_training_plan`` et ``save_plan``.

    Retourne ``(plan_id, plan, context)`` où ``context`` contient les
    métadonnées utiles aux appelants (CTL courant, date cible, type d'objectif,
    flag availability_loaded, jours utilisés). Cette fonction est appelée à la
    fois par le tool LLM `generate_training_plan` et par l'endpoint
    `POST /api/plan`.

    Lève ``PlanGenerationError`` sur input invalide / plan vide.
    Lève ``AvailabilityError`` si `availability.yaml` est mal formé.
    """
    from domestique_ai.athlete_context import context_from_env
    from domestique_ai.llm.availability import load_availability
    from domestique_ai.llm.objectives import load_objective
    from domestique_ai.llm.tools import (
        calculate_ctl_atl_tsb,
        fetch_activities_from_db,
    )
    from domestique_ai.processing.plan_builder import days_used

    ctx = ctx or context_from_env()

    if sessions_per_week not in (2, 3, 4, 5, 6, 7):
        raise PlanGenerationError(
            f"sessions_per_week doit être entre 2 et 7, reçu {sessions_per_week}."
        )

    activities = fetch_activities_from_db(ctx=ctx)
    today = _dt.date.today()
    curves = calculate_ctl_atl_tsb(activities, end_date=today)
    ctl_current = float(curves[-1]["CTL"]) if curves else 0.0

    objective = load_objective(ctx.objective_path)
    target_date: _dt.date | None = None
    target_event_type = "cyclosportive"
    if objective is not None:
        target_event_type = objective.type
        if objective.date:
            try:
                target_date = _dt.date.fromisoformat(objective.date)
            except ValueError:
                target_date = None

    availability = load_availability(ctx.availability_path)

    plan = build_training_plan(
        target_date=target_date,
        ctl_current=ctl_current,
        sessions_per_week=sessions_per_week,
        availability=availability,
        target_event_type=target_event_type,
        focus=focus,
        start_date=today,
    )

    if not plan:
        raise PlanGenerationError("Aucune séance générée (date cible déjà passée ?).")

    plan_id = save_plan(
        plan,
        target_date=target_date,
        target_event_type=target_event_type,
        sessions_per_week=sessions_per_week,
        db_path=ctx.db_path,
    )

    context = {
        "ctl_current": round(ctl_current, 1),
        "target_date": target_date.isoformat() if target_date else None,
        "target_event_type": target_event_type,
        "availability_loaded": availability is not None,
        "days_used": days_used(plan),
    }
    return plan_id, plan, context
