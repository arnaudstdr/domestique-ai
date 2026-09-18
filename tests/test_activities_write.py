"""Tests de l'ajout manuel, de la suppression et du fallback ``external_id``."""

from __future__ import annotations

import sqlite3
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
    db = tmp_path / "api_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("STRAVA_HR_REST", "50")
    monkeypatch.setenv("STRAVA_HR_MAX", "190")
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def _payload(**overrides) -> dict:
    base = {
        "date": "2026-06-01T08:00:00Z",
        "sport_type": "Ride",
        "duration_sec": 3600,
        "distance_km": 30.0,
        "elevation_m": 200,
        "avg_hr": 140,
        "max_hr": 170,
        "avg_power": 200,
        "name": "Sortie manuelle",
    }
    base.update(overrides)
    return base


def test_create_manual_activity(client: TestClient) -> None:
    r = client.post("/api/activities", json=_payload())
    assert r.status_code == 201
    body = r.json()
    assert isinstance(body["external_id"], int)
    assert body["source"] == "manual"
    assert body["duration_sec"] == 3600
    assert body["distance_km"] == 30.0
    assert body["tss"] > 0  # hr-TSS (profil HR configuré)


def test_create_manual_activity_invalid_date(client: TestClient) -> None:
    r = client.post("/api/activities", json=_payload(date="pas-une-date"))
    assert r.status_code == 400


def test_manual_activity_resolvable_and_listed(client: TestClient) -> None:
    created = client.post("/api/activities", json=_payload()).json()
    ext_id = created["external_id"]

    detail = client.get(f"/api/activities/{ext_id}")
    assert detail.status_code == 200
    assert detail.json()["activity"]["name"] == "Sortie manuelle"

    listed = client.get("/api/activities?page=1&page_size=20").json()
    assert listed["total"] == 1
    assert listed["items"][0]["external_id"] == ext_id


def test_manual_activity_has_no_streams(client: TestClient) -> None:
    created = client.post("/api/activities", json=_payload()).json()
    r = client.get(f"/api/activities/{created['external_id']}/streams")
    assert r.status_code == 404
    assert "manuelle" in r.json()["detail"]


def test_delete_manual_activity(client: TestClient) -> None:
    created = client.post("/api/activities", json=_payload()).json()
    ext_id = created["external_id"]

    assert client.delete(f"/api/activities/{ext_id}").status_code == 204
    assert client.get(f"/api/activities/{ext_id}").status_code == 404


def test_delete_refuses_garmin_activity(client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "api_test.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (garmin_id, date, duration, source) VALUES (?, ?, ?, ?)",
            (24212850226, "2026-06-01T08:00:00Z", 3600, "garmin"),
        )
        conn.commit()
    finally:
        conn.close()

    r = client.delete("/api/activities/24212850226")
    assert r.status_code == 403


def test_delete_unknown_activity(client: TestClient) -> None:
    assert client.delete("/api/activities/999999").status_code == 404
