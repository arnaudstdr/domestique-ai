"""Tests du rapprochement plan vs réalisé (compliance)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from domestique_ai.ingestion.db import init_db
from domestique_ai.llm.plan_storage import list_decisions, save_day_decision, save_plan
from domestique_ai.processing.compliance import compute_week_compliance, week_boundaries
from domestique_ai.processing.plan_builder import Workout


def _workout(date: str, *, kind: str = "endurance", duration_min: int = 90) -> Workout:
    return Workout(
        date=date,
        name=kind.capitalize(),
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone="z2" if kind == "endurance" else "z4",
        estimated_tss=duration_min * 1.0,
        notes="Outdoor — test",
    )


def _activity(date: str, *, duration_sec: int = 5400, tss: float = 80.0, sport: str = "Ride") -> dict:
    return {
        "date": date,
        "duration": duration_sec,
        "training_load": tss,
        "sport_type": sport,
        "garmin_id": hash(date),
        "distance": 40_000.0,
    }


def _monday() -> _dt.date:
    today = _dt.date.today()
    return today - _dt.timedelta(days=today.weekday())


def test_week_boundaries_are_monday_sunday() -> None:
    start, end = week_boundaries(_dt.date(2026, 9, 4))  # vendredi
    assert start.isoformat() == "2026-08-31"
    assert end.isoformat() == "2026-09-06"


def test_all_done(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    init_db(db)
    start = _monday()
    plan = [_workout((start + _dt.timedelta(days=1)).isoformat())]
    activities = [_activity((start + _dt.timedelta(days=1)).isoformat())]
    report = compute_week_compliance(plan, activities, week_start=start)
    assert report["done"] == 1
    assert report["missed"] == 0
    assert report["adherence_pct"] == 100.0
    assert report["realized_tss"] == 80.0


def test_missed_when_no_activity(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    init_db(db)
    start = _monday()
    plan = [_workout((start + _dt.timedelta(days=2)).isoformat())]
    report = compute_week_compliance(plan, [], week_start=start)
    assert report["done"] == 0
    assert report["missed"] == 1
    assert report["adherence_pct"] == 0.0


def test_partial_when_short_activity(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    init_db(db)
    start = _monday()
    date = (start + _dt.timedelta(days=3)).isoformat()
    plan = [_workout(date, duration_min=120)]
    # 30 min = 25 % de 120 min → partiel.
    report = compute_week_compliance(plan, [_activity(date, duration_sec=1800)], week_start=start)
    assert report["partial"] == 1
    assert report["missed"] == 0


def test_rest_decision_is_not_missed(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    init_db(db)
    start = _monday()
    date = (start + _dt.timedelta(days=1)).isoformat()
    plan = [_workout(date)]
    pid = save_plan(plan, db_path=db)
    save_day_decision(pid, date, "rest", reason="Readiness basse", db_path=db)
    decisions = list_decisions(pid, db_path=db)
    report = compute_week_compliance(plan, [], week_start=start, decisions=decisions)
    assert report["skipped_by_decision"] == 1
    assert report["missed"] == 0
    per_day = report["per_day"]
    assert per_day[1]["status"] == "rest"
    assert per_day[1]["decision"]["decision"] == "rest"


def test_indoor_workout_matches_virtual_ride_only(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    init_db(db)
    start = _monday()
    date = (start + _dt.timedelta(days=4)).isoformat()
    workout = Workout(
        date=date,
        name="Intervals HT",
        sport="cycling",
        kind="intervals",
        duration_min=60,
        target_zone="z4",
        estimated_tss=95.0,
        notes="Indoor (home trainer) — test",
    )
    outdoor = _activity(date, sport="Ride")
    indoor = _activity(date, sport="VirtualRide")
    report = compute_week_compliance([workout], [outdoor, indoor], week_start=start)
    assert report["done"] == 1
    realized = next(d for d in report["per_day"] if d["planned"])["realized"]
    assert realized["sport_type"] == "VirtualRide"


def test_delta_tss() -> None:
    start = _monday()
    date = (start + _dt.timedelta(days=1)).isoformat()
    plan = [_workout(date, duration_min=90)]  # planned_tss 90
    report = compute_week_compliance(plan, [_activity(date, tss=45.0)], week_start=start)
    assert report["planned_tss"] == 90.0
    assert report["realized_tss"] == 45.0
    assert report["tss_delta_pct"] == -50.0


def test_empty_plan() -> None:
    report = compute_week_compliance([], [], week_start=_monday())
    assert report["available"] is False
    assert report["planned_sessions"] == 0
    assert report["adherence_pct"] == 0.0