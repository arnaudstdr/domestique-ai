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
    start_date: _dt.date | None = None,
    parent_plan_id: int | None = None,
    adapt_reason: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Persiste un plan et retourne son ``plan_id``.

    Le nouveau plan est enregistré comme ``active`` — les éventuels autres plans
    ``active`` sont basculés en ``superseded`` (un seul plan actif à la fois).
    """
    payload = json.dumps([w.to_dict() for w in plan], ensure_ascii=False)
    weeks = max(1, (len(plan) // max(1, sessions_per_week or 1))) if sessions_per_week else None
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE training_plans SET status = 'superseded' WHERE status = 'active'")
        cursor = conn.execute(
            "INSERT INTO training_plans "
            "(created_at, target_date, target_event_type, sessions_per_week, weeks, "
            " payload, status, parent_plan_id, start_date, adapt_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                target_date.isoformat() if target_date else None,
                target_event_type,
                sessions_per_week,
                weeks,
                payload,
                parent_plan_id,
                start_date.isoformat() if start_date else None,
                adapt_reason,
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
            "sessions_per_week, weeks, status, parent_plan_id, start_date, adapt_reason "
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
            "status": row[6],
            "parent_plan_id": row[7],
            "start_date": row[8],
            "adapt_reason": row[9],
        }
        for row in rows
    ]


def get_plan_meta(plan_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Métadonnées d'un plan (sans payload), ou None si inconnu."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, created_at, target_date, target_event_type, "
            "sessions_per_week, weeks, status, parent_plan_id, start_date, adapt_reason "
            "FROM training_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "created_at": row[1],
        "target_date": row[2],
        "target_event_type": row[3],
        "sessions_per_week": row[4],
        "weeks": row[5],
        "status": row[6],
        "parent_plan_id": row[7],
        "start_date": row[8],
        "adapt_reason": row[9],
    }


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


def load_active_plan(db_path: Path | None = None) -> tuple[int, list[Workout]] | None:
    """Charge le plan actif (status ``active``), sinon le plus récent."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, payload FROM training_plans "
            "WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id, payload FROM training_plans ORDER BY id DESC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = json.loads(row[1])
    return int(row[0]), [Workout.from_dict(item) for item in raw]


def list_versions(plan_id: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Ligneage d'un plan : lui-même + ses ancêtres via ``parent_plan_id``."""
    versions: list[dict[str, Any]] = []
    seen: set[int] = set()
    current_id: int | None = plan_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        meta = get_plan_meta(current_id, db_path)
        if meta is None:
            break
        versions.append(meta)
        current_id = meta.get("parent_plan_id")
    return versions


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


# Décisions journalières (check du matin) — appliquées au plan en cours.
_DECISION_VALUES = ("planned", "adjusted", "rest")


def save_day_decision(
    plan_id: int,
    date: str,
    decision: str,
    *,
    workout: Workout | None = None,
    reason: str = "",
    decided_by: str = "daily_check",
    db_path: Path | None = None,
) -> int:
    """Persiste la décision coach pour un jour donné du plan (upsert).

    ``decision`` ∈ ``planned`` (faire la séance prévue), ``adjusted`` (séance
    allégée/modifiée, payload dans ``workout``), ``rest`` (repos complet).
    """
    if decision not in _DECISION_VALUES:
        raise ValueError(f"decision doit être un de {_DECISION_VALUES}, reçu {decision!r}")
    payload = json.dumps(workout.to_dict(), ensure_ascii=False) if workout else None
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO plan_decisions "
            "(plan_id, date, decision, workout_payload, reason, decided_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(plan_id, date) DO UPDATE SET "
            "decision = excluded.decision, "
            "workout_payload = excluded.workout_payload, "
            "reason = excluded.reason, "
            "decided_by = excluded.decided_by, "
            "created_at = excluded.created_at",
            (
                plan_id,
                date,
                decision,
                payload,
                reason,
                decided_by,
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM plan_decisions WHERE plan_id = ? AND date = ?", (plan_id, date)
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def get_day_decision(plan_id: int, date: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Décision du jour pour un plan, ou None."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, plan_id, date, decision, workout_payload, reason, decided_by, created_at "
            "FROM plan_decisions WHERE plan_id = ? AND date = ?",
            (plan_id, date),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    out = {
        "id": row[0],
        "plan_id": row[1],
        "date": row[2],
        "decision": row[3],
        "reason": row[5],
        "decided_by": row[6],
        "created_at": row[7],
    }
    if row[4]:
        out["workout"] = Workout.from_dict(json.loads(row[4]))
    return out


def list_decisions(
    plan_id: int, db_path: Path | None = None, *, since: str | None = None
) -> list[dict[str, Any]]:
    """Toutes les décisions d'un plan, triées par date ASC."""
    conn = _connect(db_path)
    try:
        if since:
            rows = conn.execute(
                "SELECT id, plan_id, date, decision, workout_payload, reason, decided_by, "
                "created_at FROM plan_decisions WHERE plan_id = ? AND date >= ? "
                "ORDER BY date ASC",
                (plan_id, since),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, plan_id, date, decision, workout_payload, reason, decided_by, "
                "created_at FROM plan_decisions WHERE plan_id = ? ORDER BY date ASC",
                (plan_id,),
            ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "id": row[0],
            "plan_id": row[1],
            "date": row[2],
            "decision": row[3],
            "reason": row[5],
            "decided_by": row[6],
            "created_at": row[7],
        }
        if row[4]:
            item["workout"] = Workout.from_dict(json.loads(row[4]))
        out.append(item)
    return out


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

    from domestique_ai.config import get_plan_min_ctl

    plan = build_training_plan(
        target_date=target_date,
        ctl_current=ctl_current,
        sessions_per_week=sessions_per_week,
        availability=availability,
        target_event_type=target_event_type,
        focus=focus,
        start_date=today,
        min_ctl=get_plan_min_ctl(),
    )

    if not plan:
        raise PlanGenerationError("Aucune séance générée (date cible déjà passée ?).")

    plan_start = min(_dt.date.fromisoformat(w.date) for w in plan)
    plan_id = save_plan(
        plan,
        target_date=target_date,
        target_event_type=target_event_type,
        sessions_per_week=sessions_per_week,
        start_date=plan_start,
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
