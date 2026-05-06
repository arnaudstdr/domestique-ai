"""Tests pour la persistance SQLite des plans d'entraînement."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai.llm.plan_storage import (
    delete_plan,
    list_plans,
    load_latest_plan,
    load_plan,
    save_plan,
)
from domestique_ai.processing.plan_builder import build_training_plan


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "plan.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def sample_plan():
    today = dt.date(2026, 5, 4)
    return build_training_plan(
        target_date=today + dt.timedelta(weeks=4),
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )


def test_save_and_load_plan_roundtrip(db, sample_plan):
    plan_id = save_plan(
        sample_plan,
        target_date=dt.date(2026, 6, 1),
        target_event_type="cyclosportive",
        sessions_per_week=4,
    )
    assert plan_id > 0

    restored = load_plan(plan_id)
    assert restored is not None
    assert len(restored) == len(sample_plan)
    for original, copy in zip(sample_plan, restored, strict=True):
        assert original.date == copy.date
        assert original.duration_min == copy.duration_min
        assert original.kind == copy.kind
        assert len(original.structure) == len(copy.structure)


def test_list_plans_orders_by_recent_first(db, sample_plan):
    save_plan(sample_plan, target_date=dt.date(2026, 6, 1), sessions_per_week=4)
    save_plan(sample_plan, target_date=dt.date(2026, 9, 15), sessions_per_week=3)
    plans = list_plans()
    assert len(plans) == 2
    # Le plus récent (créé en dernier) est en tête.
    assert plans[0]["created_at"] >= plans[1]["created_at"]
    assert {p["target_date"] for p in plans} == {"2026-06-01", "2026-09-15"}


def test_load_latest_plan_returns_most_recent(db, sample_plan):
    save_plan(sample_plan, sessions_per_week=4)
    last_id = save_plan(sample_plan, sessions_per_week=3)

    result = load_latest_plan()
    assert result is not None
    plan_id, plan = result
    assert plan_id == last_id
    assert len(plan) == len(sample_plan)


def test_load_latest_plan_empty_returns_none(db):
    assert load_latest_plan() is None


def test_load_unknown_plan_returns_none(db, sample_plan):
    save_plan(sample_plan, sessions_per_week=4)
    assert load_plan(9999) is None


def test_delete_plan_removes_record(db, sample_plan):
    pid = save_plan(sample_plan, sessions_per_week=4)
    assert delete_plan(pid) is True
    assert load_plan(pid) is None
    assert delete_plan(pid) is False  # déjà supprimé
