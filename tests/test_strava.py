"""Tests unitaires pour le client Strava et la persistance SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domestique_ai.ingestion.strava import (
    StravaClient,
    backfill_activity_fields,
    init_db,
    save_activity,
    snapshot_athlete_weight,
)


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
        "max_heartrate": 182.0,
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
        "max_heart_rate": 182.0,
        "avg_power": 220.0,
        "elevation_gain": 500.0,
        "distance": 35000.0,
    }


def test_save_activity_inserts_and_is_idempotent(db_path: Path, monkeypatch):
    # Le test couvre la branche TSS power : on neutralise toute config HR
    # qui pourrait basculer compute_training_load sur hr-TSS.
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    activity = {
        "id": 999,
        "date": "2025-04-01T08:00:00Z",
        "duration": 3600,
        "avg_heart_rate": 145.0,
        "max_heart_rate": 182.0,
        "avg_power": 250.0,
        "elevation_gain": 500.0,
        "distance": 35000.0,
    }
    assert save_activity(activity, db_path=db_path, ftp=250.0) is True
    assert save_activity(activity, db_path=db_path, ftp=250.0) is False

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT strava_id, max_heart_rate, training_load FROM activities"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == 999
    assert rows[0][1] == pytest.approx(182.0)
    assert rows[0][2] == pytest.approx(100.0)


def test_save_activity_skips_when_no_id(db_path: Path):
    assert save_activity({"id": None}, db_path=db_path, ftp=250.0) is False


class _StubClient(StravaClient):
    """StravaClient qui retourne des activités fixes (utilisé pour le backfill)."""

    def __init__(self, activities: list[dict]):
        super().__init__(access_token="stub")
        self._activities = activities

    def fetch_activities(self, after: int | None = None,
                         per_page: int = 200) -> list[dict]:
        return self._activities


def test_backfill_activity_fields_updates_only_existing(db_path: Path):
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 150.0, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)

    client = _StubClient([
        # Connue → doit recevoir max_heart_rate=185
        {"id": 1, "start_date": "2025-04-01T08:00:00Z",
         "elapsed_time": 3600, "average_heartrate": 150.0,
         "max_heartrate": 185.0, "average_watts": 200.0,
         "total_elevation_gain": 0, "distance": 30000},
        # Inconnue → doit être ignorée par le backfill
        {"id": 2, "start_date": "2025-04-02T08:00:00Z",
         "elapsed_time": 3600, "average_heartrate": 140.0,
         "max_heartrate": 175.0, "average_watts": 180.0,
         "total_elevation_gain": 0, "distance": 25000},
    ])
    assert backfill_activity_fields(client, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT strava_id, max_heart_rate FROM activities ORDER BY strava_id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, pytest.approx(185.0))]


def test_backfill_is_idempotent(db_path: Path):
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 150.0, "max_heart_rate": 185.0,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)

    client = _StubClient([{
        "id": 1, "start_date": "2025-04-01T08:00:00Z",
        "elapsed_time": 3600, "average_heartrate": 150.0,
        "max_heartrate": 185.0, "average_watts": 200.0,
        "total_elevation_gain": 0, "distance": 30000,
    }])
    assert backfill_activity_fields(client, db_path=db_path) == 0


def test_get_authorization_url_contains_required_params():
    url = StravaClient.get_authorization_url("123", "http://localhost/cb")
    assert "client_id=123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcb" in url
    assert "scope=activity%3Aread_all" in url
    assert "response_type=code" in url


class _AthleteStubClient(StravaClient):
    """StravaClient renvoyant un profil athlete fixé (pour les snapshots poids)."""

    def __init__(self, athlete: dict):
        super().__init__(access_token="stub")
        self._athlete = athlete

    def fetch_athlete(self) -> dict:
        return self._athlete


def test_snapshot_athlete_weight_inserts_today_value(db_path: Path):
    client = _AthleteStubClient({"weight": 73.5})
    assert snapshot_athlete_weight(client, db_path=db_path,
                                   today="2025-04-01") is True

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT date, weight FROM weight_history"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("2025-04-01", pytest.approx(73.5))]


def test_snapshot_athlete_weight_returns_false_when_missing(db_path: Path):
    assert snapshot_athlete_weight(_AthleteStubClient({"weight": None}),
                                   db_path=db_path) is False
    assert snapshot_athlete_weight(_AthleteStubClient({"weight": 0.0}),
                                   db_path=db_path) is False
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM weight_history"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_snapshot_athlete_weight_overwrites_same_day(db_path: Path):
    snapshot_athlete_weight(_AthleteStubClient({"weight": 73.5}),
                            db_path=db_path, today="2025-04-01")
    snapshot_athlete_weight(_AthleteStubClient({"weight": 74.1}),
                            db_path=db_path, today="2025-04-01")
    snapshot_athlete_weight(_AthleteStubClient({"weight": 74.2}),
                            db_path=db_path, today="2025-04-02")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT date, weight FROM weight_history ORDER BY date"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("2025-04-01", pytest.approx(74.1)),
        ("2025-04-02", pytest.approx(74.2)),
    ]
