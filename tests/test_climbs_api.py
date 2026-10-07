"""Tests API des montées (/api/climbs)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db, store_activity_streams
from domestique_ai.processing.climbs import rebuild_climbs
from domestique_ai.processing.geo import decode_polyline

STEP_SEC = 5.0
SPEED_MPS = 6.0


def _hill_payload(
    *,
    start_lat: float = 48.0,
    start_lng: float = 7.0,
    base_alt: float = 300.0,
    flat_m: float = 1000.0,
    climb_m: float = 2000.0,
    gradient_pct: float = 5.0,
    tail_m: float = 1000.0,
) -> dict:
    total = flat_m + climb_m + tail_m
    time, distance, altitude, lat, lng = [], [], [], [], []
    dist = 0.0
    t = 0.0
    while dist <= total:
        if dist < flat_m:
            alt = base_alt
        elif dist < flat_m + climb_m:
            alt = base_alt + (dist - flat_m) * gradient_pct / 100
        else:
            alt = base_alt + climb_m * gradient_pct / 100
        time.append(t)
        distance.append(dist)
        altitude.append(alt)
        lat.append(start_lat + dist / 111_000.0)
        lng.append(start_lng)
        dist += SPEED_MPS * STEP_SEC
        t += STEP_SEC
    return {"time": time, "distance": distance, "altitude": altitude, "lat": lat, "lng": lng}


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> TestClient:
    db = tmp_path / "climbs_api.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (id, strava_id, date, duration, sport_type) "
            "VALUES (1, 1, '2026-04-05T08:00:00Z', 3000, 'Ride')"
        )
        conn.commit()
    finally:
        conn.close()
    store_activity_streams(1, _hill_payload(), db_path=db)
    rebuild_climbs(db_path=db)

    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as test_client:
        yield test_client


def test_list_climbs_returns_detected_segment(client: TestClient):
    response = client.get("/api/climbs")
    assert response.status_code == 200
    climbs = response.json()["climbs"]
    assert len(climbs) == 1
    assert climbs[0]["efforts"] == 1
    assert climbs[0]["best_sec"] is not None
    assert climbs[0]["end_lat"] is not None
    assert climbs[0]["end_lng"] is not None
    assert len(decode_polyline(climbs[0]["map_polyline"])) >= 2


def test_rename_and_detail(client: TestClient):
    segment_id = client.get("/api/climbs").json()["climbs"][0]["id"]

    renamed = client.put(f"/api/climbs/{segment_id}", json={"name": "Haut-Koenigsbourg"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Haut-Koenigsbourg"

    detail = client.get(f"/api/climbs/{segment_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "Haut-Koenigsbourg"
    assert len(body["efforts_list"]) == 1
    assert body["efforts_list"][0]["duration_sec"] > 0


def test_rename_unknown_segment_404(client: TestClient):
    response = client.put("/api/climbs/999", json={"name": "X"})
    assert response.status_code == 404


def test_detail_unknown_segment_404(client: TestClient):
    assert client.get("/api/climbs/999").status_code == 404
