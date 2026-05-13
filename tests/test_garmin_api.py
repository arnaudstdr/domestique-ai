"""Tests de l'endpoint `POST /api/plan/{id}/push-garmin`.

Tous les mocks de `get_client`, `push_workout` et `schedule_workout` sont posés
ici pour éviter tout appel réseau vers Garmin Connect pendant les tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from domestique_ai.export.garmin_connect import GarminPushError
from domestique_ai.ingestion.strava import init_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "garmin_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "obj.yaml"))
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "avail.yaml"))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app) as c:
        yield c


def _parse_sse(response_text: str) -> list[dict]:
    """Extrait les payloads JSON des events SSE renvoyés par le serveur."""
    events: list[dict] = []
    for raw in response_text.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        events.append(json.loads(data))
    return events


def _create_plan(client: TestClient) -> int:
    r = client.post("/api/plan", json={"sessions_per_week": 3})
    assert r.status_code == 201
    return r.json()["id"]


def test_push_garmin_not_found(client: TestClient) -> None:
    r = client.post("/api/plan/99999/push-garmin", json={"schedule": True})
    assert r.status_code == 404


def test_push_garmin_auth_failure_emits_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = _create_plan(client)

    def fake_get_client():
        raise GarminPushError("Token expiré, relance le setup CLI.")

    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.get_client", fake_get_client
    )

    r = client.post(f"/api/plan/{plan_id}/push-garmin", json={"schedule": True})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert "error" in types
    assert types[-1] == "done"
    error_event = next(e for e in events if e["type"] == "error")
    assert "Token" in error_event["value"]


def test_push_garmin_success_streams_progress_and_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = _create_plan(client)

    fake_client = MagicMock()
    fake_client.schedule_workout.return_value = None
    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.get_client", lambda: fake_client
    )

    counter = {"i": 0}

    def fake_push_workout(client, workout, hr_rest=None, hr_max=None):  # noqa: ANN001, ARG001
        counter["i"] += 1
        return 1000 + counter["i"]

    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.push_workout", fake_push_workout
    )

    r = client.post(f"/api/plan/{plan_id}/push-garmin", json={"schedule": True})
    assert r.status_code == 200
    events = _parse_sse(r.text)

    # Le premier event doit être `start` avec le total des séances.
    start = events[0]
    assert start["type"] == "start"
    total = start["total"]
    assert total > 0

    progress_events = [e for e in events if e["type"] == "progress"]
    result_events = [e for e in events if e["type"] == "result"]
    done_event = events[-1]

    # Une paire (progress, result) par séance, dans l'ordre.
    assert len(progress_events) == total
    assert len(result_events) == total
    assert all(r["scheduled"] is True for r in result_events)
    assert all("workout_id" in r and r["workout_id"] for r in result_events)

    assert done_event["type"] == "done"
    assert done_event["uploaded"] == total
    assert done_event["errors"] == 0

    # schedule_workout doit avoir été appelé une fois par séance.
    assert fake_client.schedule_workout.call_count == total


def test_push_garmin_without_schedule_skips_calendar(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = _create_plan(client)

    fake_client = MagicMock()
    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.get_client", lambda: fake_client
    )
    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.push_workout",
        lambda *a, **kw: 4242,
    )

    r = client.post(f"/api/plan/{plan_id}/push-garmin", json={"schedule": False})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert all(e.get("scheduled") in (False, None) for e in events if e["type"] == "result")
    fake_client.schedule_workout.assert_not_called()


def test_push_garmin_per_workout_error_continues(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si `push_workout` échoue sur une séance, on enchaîne sur les suivantes."""
    plan_id = _create_plan(client)

    fake_client = MagicMock()
    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.get_client", lambda: fake_client
    )

    counter = {"i": 0}

    def flaky_push(*a, **kw):  # noqa: ANN002, ANN003, ARG001
        counter["i"] += 1
        if counter["i"] == 1:
            raise GarminPushError("payload rejeté")
        return 9000 + counter["i"]

    monkeypatch.setattr(
        "domestique_ai.api.routers.plan.push_workout", flaky_push
    )

    r = client.post(f"/api/plan/{plan_id}/push-garmin", json={"schedule": False})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    results = [e for e in events if e["type"] == "result"]
    assert results[0].get("error") == "payload rejeté"
    assert results[0]["workout_id"] is None
    # Les autres séances doivent être uploadées malgré l'erreur initiale.
    assert all(r.get("workout_id") for r in results[1:])
    done = events[-1]
    assert done["type"] == "done"
    assert done["errors"] == 1
    assert done["uploaded"] == len(results) - 1
