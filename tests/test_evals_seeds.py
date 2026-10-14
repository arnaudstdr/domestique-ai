"""Tests des seeds synthétiques (``evals.lib.seeds``)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domestique_ai.llm.availability import load_availability
from evals.lib.seeds import seed_scenario


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_seed_empty_has_no_activities(tmp_path):
    paths = seed_scenario(tmp_path / "empty", "empty")
    assert paths.db_path.exists()
    assert _count(paths.db_path, "activities") == 0
    assert not paths.objective_path.exists()
    assert not paths.availability_path.exists()


def test_seed_normal_writes_activities_and_files(tmp_path):
    paths = seed_scenario(tmp_path / "normal", "normal")
    assert _count(paths.db_path, "activities") >= 10
    availability = load_availability(paths.availability_path)
    assert availability is not None
    assert availability.get(2) is not None  # mercredi
    assert "cyclosportive" in paths.objective_path.read_text(encoding="utf-8")
    assert "ftp: 250" in paths.profile_path.read_text(encoding="utf-8")


def test_seed_overtraining_adds_load_and_fatigue_markers(tmp_path):
    normal = seed_scenario(tmp_path / "n", "normal")
    over = seed_scenario(tmp_path / "o", "overtraining")
    assert _count(over.db_path, "activities") > _count(normal.db_path, "activities")
    assert _count(over.db_path, "morning_metrics") > 0


def test_seed_comeback_stops_before_april(tmp_path):
    paths = seed_scenario(tmp_path / "comeback", "comeback")
    conn = sqlite3.connect(paths.db_path)
    try:
        latest = conn.execute("SELECT MAX(date) FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert latest is not None
    assert latest < "2026-04-01"


def test_seed_busy_week_limits_availability(tmp_path):
    paths = seed_scenario(tmp_path / "busy", "busy_week")
    availability = load_availability(paths.availability_path)
    assert availability is not None
    assert len(availability.days) == 2


def test_seed_unknown_scenario_raises(tmp_path):
    with pytest.raises(ValueError, match="inconnu"):
        seed_scenario(tmp_path / "x", "nope")
