"""Tests pour le push Garmin Connect (mock complet — pas de réseau)."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from domestique_ai.export.garmin_connect import (
    GarminPushError,
    build_workout_payload,
    push_plan,
    push_workout,
)
from domestique_ai.processing.plan_builder import build_training_plan


@pytest.fixture
def sample_plan():
    today = dt.date(2026, 5, 4)
    return build_training_plan(
        target_date=today + dt.timedelta(weeks=4),
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )


# ---- Construction du payload -------------------------------------------------


def test_payload_uses_zone_index_when_no_hr_provided(sample_plan):
    payload = build_workout_payload(sample_plan[0])
    assert payload["sportType"] == {"sportTypeId": 2, "sportTypeKey": "cycling"}
    assert payload["estimatedDurationInSecs"] > 0
    assert len(payload["workoutSegments"]) == 1
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert steps and len(steps) == len(sample_plan[0].structure)
    for step in steps:
        assert step["type"] == "ExecutableStepDTO"
        assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
        assert step["zoneNumber"] in (1, 2, 3, 4, 5)
        assert "targetValueOne" not in step  # pas de mode BPM


def test_payload_uses_custom_bpm_when_hr_provided(sample_plan):
    hr_rest = 50
    hr_max = 190
    payload = build_workout_payload(sample_plan[0], hr_rest=hr_rest, hr_max=hr_max)
    steps = payload["workoutSegments"][0]["workoutSteps"]
    custom_steps = [s for s in steps if "targetValueOne" in s]
    assert custom_steps, "Au moins un step doit utiliser des bornes BPM custom"
    for step in custom_steps:
        # Bornes Karvonen valides : low ∈ [hr_rest, hr_max], low < high.
        assert hr_rest <= step["targetValueOne"] <= hr_max
        assert step["targetValueOne"] < step["targetValueTwo"] <= hr_max
        assert "zoneNumber" not in step


def test_payload_step_orders_are_sequential(sample_plan):
    payload = build_workout_payload(sample_plan[0])
    steps = payload["workoutSegments"][0]["workoutSteps"]
    orders = [s["stepOrder"] for s in steps]
    assert orders == list(range(1, len(orders) + 1))


def test_payload_maps_phases_to_garmin_step_types(sample_plan):
    # Trouver une séance avec un warmup explicite
    interval_workout = next(w for w in sample_plan if any(s.phase == "warmup" for s in w.structure))
    payload = build_workout_payload(interval_workout)
    keys = [s["stepType"]["stepTypeKey"] for s in payload["workoutSegments"][0]["workoutSteps"]]
    assert keys[0] == "warmup"
    assert keys[-1] in ("cooldown", "interval")  # selon le type de séance


# ---- push_workout / push_plan avec client mocké -----------------------------


def _mock_client(workout_id_seq: list[int] | None = None) -> MagicMock:
    """Construit un client Garmin mocké avec upload + schedule."""
    client = MagicMock()
    seq = list(workout_id_seq or [])

    def _upload(payload):
        wid = seq.pop(0) if seq else 1000
        return {"workoutId": wid, "ownerId": 42}

    client.upload_workout.side_effect = _upload
    client.schedule_workout.return_value = {"ok": True}
    return client


def test_push_workout_returns_workout_id(sample_plan):
    client = _mock_client([12345])
    workout_id = push_workout(client, sample_plan[0])
    assert workout_id == 12345
    client.upload_workout.assert_called_once()


def test_push_workout_raises_on_missing_id(sample_plan):
    client = MagicMock()
    client.upload_workout.return_value = {"unexpected": "shape"}
    with pytest.raises(GarminPushError):
        push_workout(client, sample_plan[0])


def test_push_plan_uploads_and_schedules_each_workout(sample_plan):
    client = _mock_client([100 + i for i in range(len(sample_plan))])
    results = push_plan(sample_plan, schedule=True, client=client)
    assert len(results) == len(sample_plan)
    assert all(r["workout_id"] for r in results)
    assert all(r["scheduled"] for r in results)
    assert all(r["url"].startswith("https://connect.garmin.com/") for r in results)
    assert client.upload_workout.call_count == len(sample_plan)
    assert client.schedule_workout.call_count == len(sample_plan)
    # Schedule doit être appelé avec la date de chaque séance.
    scheduled_dates = [call.args[1] for call in client.schedule_workout.call_args_list]
    assert scheduled_dates == [w.date for w in sample_plan]


def test_push_plan_skip_schedule(sample_plan):
    client = _mock_client()
    results = push_plan(sample_plan, schedule=False, client=client)
    assert all(r["scheduled"] is False for r in results)
    client.schedule_workout.assert_not_called()


def test_push_plan_records_upload_failure_and_continues(sample_plan):
    client = MagicMock()
    # 1ʳᵉ séance échoue, suivantes OK.
    responses = [Exception("boom")] + [
        {"workoutId": 100 + i, "ownerId": 42} for i in range(1, len(sample_plan))
    ]
    client.upload_workout.side_effect = responses
    client.schedule_workout.return_value = {"ok": True}

    results = push_plan(sample_plan[:3], schedule=True, client=client)
    assert len(results) == 3
    assert results[0]["workout_id"] is None
    assert "error" in results[0]
    assert results[1]["workout_id"] == 101
    assert results[2]["workout_id"] == 102


def test_push_plan_records_schedule_failure_but_keeps_workout(sample_plan):
    client = MagicMock()
    client.upload_workout.return_value = {"workoutId": 999, "ownerId": 42}
    client.schedule_workout.side_effect = Exception("calendar conflict")

    results = push_plan(sample_plan[:1], schedule=True, client=client)
    assert results[0]["workout_id"] == 999
    assert results[0]["scheduled"] is False
    assert "schedule failed" in results[0]["error"]


def test_push_plan_progress_callback_invoked(sample_plan):
    client = _mock_client()
    calls: list[tuple[int, int, str]] = []

    def _progress(idx, total, workout):
        calls.append((idx, total, workout.name))

    push_plan(sample_plan[:3], schedule=False, client=client, progress=_progress)
    assert len(calls) == 3
    assert calls[0][0] == 0 and calls[0][1] == 3
    assert calls[-1][0] == 2


def test_push_plan_progress_exception_does_not_break_push(sample_plan):
    """Une exception dans le callback UI ne doit pas casser le push."""
    client = _mock_client()

    def _bad_progress(idx, total, workout):
        raise RuntimeError("ui dead")

    results = push_plan(sample_plan[:2], schedule=False, client=client, progress=_bad_progress)
    assert len(results) == 2
    assert all(r["workout_id"] for r in results)
