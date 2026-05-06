"""Tests pour l'export FIT (Garmin Workout)."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest

from domestique_ai.export.fit import plan_to_zip, workout_to_fit
from domestique_ai.processing.plan_builder import build_training_plan


def _sample_plan(sessions_per_week: int = 4):
    today = dt.date(2026, 5, 4)
    return build_training_plan(
        target_date=today + dt.timedelta(weeks=4),
        ctl_current=40.0,
        sessions_per_week=sessions_per_week,
        start_date=today,
    )


def test_workout_to_fit_produces_valid_header_and_records():
    fit_tool = pytest.importorskip("fit_tool.fit_file")
    plan = _sample_plan()
    fit_bytes = workout_to_fit(plan[0], hr_rest=50, hr_max=190)
    assert fit_bytes[:1] == b"\x0c"  # taille du header FIT (12 octets)

    fit_file = fit_tool.FitFile.from_bytes(fit_bytes)
    records = list(fit_file.records)
    # On veut au minimum un FileId, un WorkoutMessage et 1 step.
    types = {type(r.message).__name__ for r in records}
    assert "FileIdMessage" in types
    assert "WorkoutMessage" in types
    assert "WorkoutStepMessage" in types

    steps = [r for r in records if type(r.message).__name__ == "WorkoutStepMessage"]
    assert len(steps) == len(plan[0].structure)


def test_workout_to_fit_uses_custom_bpm_when_hr_provided():
    plan = _sample_plan()
    fit_tool = pytest.importorskip("fit_tool.fit_file")
    fit_bytes = workout_to_fit(plan[0], hr_rest=50, hr_max=190)
    fit_file = fit_tool.FitFile.from_bytes(fit_bytes)
    steps = [
        r.message for r in fit_file.records
        if type(r.message).__name__ == "WorkoutStepMessage"
    ]
    # Au moins un step doit avoir une plage HR custom (BPM ≥ 100).
    custom_lows = [s.custom_target_heart_rate_low for s in steps]
    assert any(low and low >= 100 for low in custom_lows)


def test_workout_to_fit_falls_back_to_zone_index_when_no_hr():
    plan = _sample_plan()
    fit_tool = pytest.importorskip("fit_tool.fit_file")
    fit_bytes = workout_to_fit(plan[0])
    fit_file = fit_tool.FitFile.from_bytes(fit_bytes)
    steps = [
        r.message for r in fit_file.records
        if type(r.message).__name__ == "WorkoutStepMessage"
    ]
    assert steps
    # Tous les steps doivent référencer une zone HR standard 1..5.
    zones = [s.target_hr_zone for s in steps]
    assert all(z is not None and 1 <= z <= 5 for z in zones)


def test_plan_to_zip_contains_one_fit_per_workout():
    plan = _sample_plan(sessions_per_week=3)
    zip_bytes = plan_to_zip(plan, hr_rest=50, hr_max=190)
    assert zip_bytes[:2] == b"PK"  # signature ZIP

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert len(names) == len(plan)
        for name in names:
            assert name.endswith(".fit")
            assert len(zf.read(name)) > 50  # chaque .fit a un contenu non trivial
