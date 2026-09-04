"""Tests des endpoints ``/api/availability``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "avail_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "avail.yaml"))
    init_db(db)

    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def test_get_availability_empty(client: TestClient) -> None:
    r = client.get("/api/availability")
    assert r.status_code == 200
    assert r.json() is None


def test_put_availability_roundtrips(client: TestClient) -> None:
    payload = {
        "days": {
            "wednesday": {"max_duration_min": 90, "context": "indoor"},
            "thursday": {"max_duration_min": 90, "context": "indoor"},
            "saturday": {"max_duration_min": 240, "context": "outdoor"},
            "sunday": {"max_duration_min": 240, "context": "outdoor"},
        },
        "preferences": {
            "long_endurance_day": "sunday",
            "intervals_day": "thursday",
        },
    }
    r = client.put("/api/availability", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert set(body["days"].keys()) == {"wednesday", "thursday", "saturday", "sunday"}
    assert body["preferences"]["long_endurance_day"] == "sunday"
    assert body["preferences"]["intervals_day"] == "thursday"

    r2 = client.get("/api/availability")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["days"]["sunday"]["max_duration_min"] == 240
    assert body2["days"]["sunday"]["context"] == "outdoor"


def test_put_availability_rejects_too_short(client: TestClient) -> None:
    r = client.put(
        "/api/availability",
        json={
            "days": {"monday": {"max_duration_min": 10, "context": "indoor"}},
        },
    )
    assert r.status_code == 422


def test_put_availability_rejects_bad_context(client: TestClient) -> None:
    r = client.put(
        "/api/availability",
        json={
            "days": {"monday": {"max_duration_min": 60, "context": "amphibie"}},
        },
    )
    assert r.status_code == 422


def test_put_availability_orphan_preference_silently_ignored(
    client: TestClient,
) -> None:
    """Une préférence pointant un jour non listé doit être normalisée en None."""
    payload = {
        "days": {"saturday": {"max_duration_min": 180, "context": "outdoor"}},
        "preferences": {"long_endurance_day": "monday"},
    }
    r = client.put("/api/availability", json=payload)
    assert r.status_code == 200
    body = r.json()
    # `monday` n'est pas dans `days` → la préférence est filtrée.
    assert body["preferences"] is None or body["preferences"]["long_endurance_day"] is None
