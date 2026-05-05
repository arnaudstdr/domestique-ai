"""Tests unitaires pour le client Strava et la persistance SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domestique_ai.ingestion.strava import StravaClient, init_db, save_activity


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


def test_init_db_creates_table(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activities'"
        )
        assert cursor.fetchone() is not None
    finally:
        conn.close()


def test_extract_activity_data():
    raw = {
        "id": 12345,
        "start_date": "2025-04-01T08:00:00Z",
        "elapsed_time": 3600,
        "average_heartrate": 145.0,
        "average_watts": 220.0,
        "total_elevation_gain": 500.0,
        "distance": 35000.0,
    }
    client = StravaClient(access_token="x")
    extracted = client.extract_activity_data(raw)
    assert extracted == {
        "id": 12345,
        "date": "2025-04-01T08:00:00Z",
        "duration": 3600,
        "avg_heart_rate": 145.0,
        "avg_power": 220.0,
        "elevation_gain": 500.0,
        "distance": 35000.0,
    }


def test_save_activity_inserts_and_is_idempotent(db_path: Path):
    activity = {
        "id": 999,
        "date": "2025-04-01T08:00:00Z",
        "duration": 3600,
        "avg_heart_rate": 145.0,
        "avg_power": 250.0,
        "elevation_gain": 500.0,
        "distance": 35000.0,
    }
    assert save_activity(activity, db_path=db_path, ftp=250.0) is True
    assert save_activity(activity, db_path=db_path, ftp=250.0) is False

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT strava_id, training_load FROM activities").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == 999
    assert rows[0][1] == pytest.approx(100.0)


def test_save_activity_skips_when_no_id(db_path: Path):
    assert save_activity({"id": None}, db_path=db_path, ftp=250.0) is False


def test_get_authorization_url_contains_required_params():
    url = StravaClient.get_authorization_url("123", "http://localhost/cb")
    assert "client_id=123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcb" in url
    assert "scope=activity%3Aread_all" in url
    assert "response_type=code" in url
