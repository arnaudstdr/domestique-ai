"""Tests de la revue hebdomadaire (décision + re-plan versionné)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import get_sync_meta, init_db
from domestique_ai.llm.plan_storage import (
    get_plan_meta,
    load_plan,
    save_plan,
)
from domestique_ai.llm.weekly_review import (
    _fallback_decision,
    _iso_week_key,
    _next_monday,
    run_weekly_review,
)
from domestique_ai.processing.morning_metrics import save_morning_entry
from domestique_ai.processing.plan_builder import Workout


@pytest.fixture()
def ctx(tmp_path: Path) -> AthleteContext:
    db = tmp_path / "weekly.db"
    init_db(db)
    return AthleteContext(
        db_path=db,
        profile_path=tmp_path / "profile.yaml",
        objective_path=tmp_path / "objective.yaml",
        availability_path=tmp_path / "availability.yaml",
        ftp=250.0,
        hr_rest=60.0,
        hr_max=185.0,
        sex="M",
        lthr_pct=0.88,
    )


def _workout(date: str, *, kind: str = "endurance", duration_min: int = 90) -> Workout:
    return Workout(
        date=date,
        name=kind.capitalize(),
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone="z2" if kind == "endurance" else "z4",
        estimated_tss=duration_min * 1.0,
        notes="Outdoor",
    )


def _plan_covering(ctx: AthleteContext, start: _dt.date, weeks: int = 5) -> int:
    workouts = []
    for w in range(weeks):
        workouts.append(_workout((start + _dt.timedelta(days=w * 7 + 1)).isoformat()))
        workouts.append(_workout((start + _dt.timedelta(days=w * 7 + 3)).isoformat(), kind="tempo", duration_min=60))
    return save_plan(workouts, db_path=ctx.db_path)


def _seed_morning(ctx: AthleteContext, today: _dt.date, readiness: int = 80) -> None:
    for i in range(14, 0, -1):
        day = (today - _dt.timedelta(days=i)).isoformat()
        save_morning_entry(day, hrv_ms=60.0, resting_hr=55.0, sleep_hours=7.5, readiness_score=readiness, db_path=ctx.db_path)


# --- Décision déterministe --------------------------------------------------


def test_decision_reduce_when_missed() -> None:
    action, factor, _ = _fallback_decision({"compliance": {"planned_sessions": 4, "missed": 2, "adherence_pct": 50.0}})
    assert action == "reduce"
    assert factor == 0.85


def test_decision_reduce_when_low_readiness() -> None:
    action, factor, _ = _fallback_decision(
        {"compliance": {"planned_sessions": 4, "missed": 0, "adherence_pct": 100.0}, "morning": {"readiness_median": 45}}
    )
    assert action == "reduce"


def test_decision_progress_when_conform() -> None:
    action, factor, _ = _fallback_decision(
        {"compliance": {"planned_sessions": 4, "missed": 0, "adherence_pct": 100.0}, "morning": {"readiness_median": 80}}
    )
    assert action == "progress"
    assert factor == 1.05


def test_decision_maintain_when_no_plan() -> None:
    action, _, _ = _fallback_decision({"compliance": {"planned_sessions": 0, "missed": 0, "adherence_pct": 0.0}})
    assert action == "maintain"


# --- run_weekly_review ------------------------------------------------------


def test_review_replans_creates_version(ctx: AthleteContext) -> None:
    today = _dt.date.today()
    last_monday = today - _dt.timedelta(days=today.weekday() + 7)
    parent_id = _plan_covering(ctx, last_monday)
    _seed_morning(ctx, today)

    result = run_weekly_review(today, ctx=ctx, use_llm=False, force=True)
    assert result["skipped"] is False
    assert result["replanned"] is True
    assert result["parent_plan_id"] == parent_id
    assert result["decision"] in ("reduce", "maintain", "progress")
    new_id = result["new_plan_id"]
    assert new_id is not None and new_id != parent_id

    # L'ancien plan est superseded, le nouveau actif.
    assert get_plan_meta(parent_id, ctx.db_path)["status"] == "superseded"
    assert get_plan_meta(new_id, ctx.db_path)["status"] == "active"
    assert get_plan_meta(new_id, ctx.db_path)["parent_plan_id"] == parent_id
    assert get_plan_meta(new_id, ctx.db_path)["adapt_reason"]

    # Le nouveau plan commence lundi prochain.
    new_plan = load_plan(new_id, ctx.db_path)
    assert new_plan is not None
    assert _dt.date.fromisoformat(new_plan[0].date) >= _next_monday(today)

    # Flag d'idempotence posé.
    assert get_sync_meta("weekly_review_last_week", ctx.db_path) == _iso_week_key(today)


def test_review_idempotent_within_week(ctx: AthleteContext) -> None:
    today = _dt.date.today()
    last_monday = today - _dt.timedelta(days=today.weekday() + 7)
    _plan_covering(ctx, last_monday)
    _seed_morning(ctx, today)

    first = run_weekly_review(today, ctx=ctx, use_llm=False, force=False)
    assert first["replanned"] is True
    second = run_weekly_review(today, ctx=ctx, use_llm=False, force=False)
    assert second["skipped"] is True
    # Le flag pose par le 1er appel n'a pas recréé de 2e version.
    third = run_weekly_review(today, ctx=ctx, use_llm=False, force=True)
    assert third["replanned"] is True


def test_review_without_active_plan(ctx: AthleteContext) -> None:
    result = run_weekly_review(_dt.date.today(), ctx=ctx, use_llm=False, force=True)
    assert result["replanned"] is False
    assert result["decision"] == "maintain"


def test_review_reduce_scales_durations(ctx: AthleteContext) -> None:
    today = _dt.date.today()
    last_monday = today - _dt.timedelta(days=today.weekday() + 7)
    _plan_covering(ctx, last_monday, weeks=4)
    # Aucune activité réalisée → toutes les séances manquées → reduce.
    result = run_weekly_review(today, ctx=ctx, use_llm=False, force=True)
    assert result["decision"] == "reduce"
    assert result["replanned"] is True
    new_plan = load_plan(result["new_plan_id"], ctx.db_path)
    assert new_plan is not None
    # Les durées de la 1re semaine sont réduites (~85 % de la base).
    first_week = [w for w in new_plan if _next_monday(today).isoformat() <= w.date < (_next_monday(today) + _dt.timedelta(days=7)).isoformat()]
    assert first_week
    assert all(w.duration_min < 100 for w in first_week if w.kind == "endurance")