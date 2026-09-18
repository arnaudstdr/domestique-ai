"""Tests de l'édition d'activités (PATCH nom/type/commentaire/RPE)."""

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
        "avg_hr": 140,
        "name": "Sortie manuelle",
    }
    base.update(overrides)
    return base


def _create(client: TestClient, **overrides) -> int:
    r = client.post("/api/activities", json=_payload(**overrides))
    assert r.status_code == 201
    return r.json()["external_id"]


def test_manual_create_persists_notes_and_rpe(client: TestClient) -> None:
    ext_id = _create(client, notes="Bonne sensation", rpe=6)
    body = client.get(f"/api/activities/{ext_id}").json()["activity"]
    assert body["notes"] == "Bonne sensation"
    assert body["rpe"] == 6


def test_patch_updates_all_editable_fields(client: TestClient) -> None:
    ext_id = _create(client)
    r = client.patch(
        f"/api/activities/{ext_id}",
        json={
            "name": "Col du Galibier",
            "sport_type": "GravelRide",
            "notes": "Très chaud",
            "rpe": 9,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Col du Galibier"
    assert body["sport_type"] == "GravelRide"
    assert body["notes"] == "Très chaud"
    assert body["rpe"] == 9

    detail = client.get(f"/api/activities/{ext_id}").json()["activity"]
    assert detail["name"] == "Col du Galibier"
    assert detail["sport_type"] == "GravelRide"
    assert detail["notes"] == "Très chaud"
    assert detail["rpe"] == 9


def test_patch_is_partial(client: TestClient) -> None:
    ext_id = _create(client, notes="Note initiale", rpe=4)
    r = client.patch(f"/api/activities/{ext_id}", json={"name": "Renommée"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renommée"
    assert body["notes"] == "Note initiale"
    assert body["rpe"] == 4
    assert body["sport_type"] == "Ride"


def test_patch_clears_fields_with_null(client: TestClient) -> None:
    ext_id = _create(client, notes="À effacer", rpe=7)
    r = client.patch(f"/api/activities/{ext_id}", json={"notes": None, "rpe": None})
    assert r.status_code == 200
    assert r.json()["notes"] is None
    assert r.json()["rpe"] is None


def test_patch_blank_string_becomes_null(client: TestClient) -> None:
    ext_id = _create(client, notes="Texte")
    r = client.patch(f"/api/activities/{ext_id}", json={"notes": "   ", "name": "  "})
    assert r.status_code == 200
    assert r.json()["notes"] is None
    assert r.json()["name"] is None


def test_patch_empty_body_is_noop(client: TestClient) -> None:
    ext_id = _create(client, notes="Inchangée", rpe=5)
    r = client.patch(f"/api/activities/{ext_id}", json={})
    assert r.status_code == 200
    assert r.json()["notes"] == "Inchangée"
    assert r.json()["rpe"] == 5


def test_patch_garmin_activity(client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "api_test.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (garmin_id, date, duration, sport_type, name, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (24212850226, "2026-06-01T08:00:00Z", 3600, "Ride", "Garmin Ride", "garmin"),
        )
        conn.commit()
    finally:
        conn.close()

    r = client.patch(
        "/api/activities/24212850226",
        json={"name": "Ma sortie", "sport_type": "MountainBikeRide", "notes": "VTT", "rpe": 8},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["external_id"] == 24212850226
    assert body["name"] == "Ma sortie"
    assert body["sport_type"] == "MountainBikeRide"
    assert body["notes"] == "VTT"
    assert body["rpe"] == 8


def test_patch_unknown_activity_returns_404(client: TestClient) -> None:
    assert client.patch("/api/activities/999999", json={"name": "x"}).status_code == 404


def test_patch_rpe_out_of_range_is_rejected(client: TestClient) -> None:
    ext_id = _create(client)
    assert client.patch(f"/api/activities/{ext_id}", json={"rpe": 0}).status_code == 422
    assert client.patch(f"/api/activities/{ext_id}", json={"rpe": 11}).status_code == 422


def test_patch_unknown_field_is_rejected(client: TestClient) -> None:
    ext_id = _create(client)
    assert client.patch(f"/api/activities/{ext_id}", json={"tss": 999}).status_code == 422


def test_migration_adds_notes_and_rpe_columns(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE activities ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, strava_id INTEGER, garmin_id INTEGER, "
            "date TEXT, sport_type TEXT, source TEXT, source_uid TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    finally:
        conn.close()
    assert {"notes", "rpe"} <= cols
