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


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None,
                 headers: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_fetch_streams_full_parses_keys(monkeypatch):
    captured: dict = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _MockResponse(200, {
            "latlng": {"data": [[45.0, 4.0], [45.1, 4.1]]},
            "altitude": {"data": [200.0, 201.0]},
            "time": {"data": [0, 5]},
            "heartrate": {"data": [120, 130]},
        })

    monkeypatch.setattr("domestique_ai.ingestion.strava.requests.get", fake_get)
    client = StravaClient(access_token="x")
    streams = client.fetch_streams_full(
        12345, ["latlng", "altitude", "time", "heartrate", "watts"]
    )

    assert streams is not None
    assert "12345/streams" in captured["url"]
    assert captured["params"]["keys"] == "latlng,altitude,time,heartrate,watts"
    assert captured["params"]["key_by_type"] == "true"
    assert streams["latlng"] == [[45.0, 4.0], [45.1, 4.1]]
    assert streams["altitude"] == [200.0, 201.0]
    assert streams["time"] == [0, 5]
    assert streams["heartrate"] == [120, 130]
    # `watts` absent côté Strava : doit être omis du résultat
    assert "watts" not in streams


def test_fetch_streams_full_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.ingestion.strava.requests.get",
        lambda *a, **kw: _MockResponse(404),
    )
    client = StravaClient(access_token="x")
    assert client.fetch_streams_full(1, ["latlng"]) is None


def test_fetch_streams_full_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.ingestion.strava.requests.get",
        lambda *a, **kw: _MockResponse(200, {}),
    )
    client = StravaClient(access_token="x")
    assert client.fetch_streams_full(1, ["latlng"]) is None


def test_fetch_streams_full_retries_on_429(monkeypatch):
    calls = {"n": 0}

    def fake_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _MockResponse(429, headers={"Retry-After": "0"})
        return _MockResponse(200, {"latlng": {"data": [[1.0, 2.0]]}})

    monkeypatch.setattr("domestique_ai.ingestion.strava.requests.get", fake_get)
    monkeypatch.setattr("domestique_ai.ingestion.strava.time.sleep", lambda _s: None)
    client = StravaClient(access_token="x")
    streams = client.fetch_streams_full(1, ["latlng"])
    assert calls["n"] == 2
    assert streams == {"latlng": [[1.0, 2.0]]}


def test_fetch_activity_summary_returns_dict(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.ingestion.strava.requests.get",
        lambda *a, **kw: _MockResponse(200, {"id": 1, "name": "Sortie"}),
    )
    client = StravaClient(access_token="x")
    summary = client.fetch_activity_summary(1)
    assert summary == {"id": 1, "name": "Sortie"}


def test_fetch_activity_summary_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.ingestion.strava.requests.get",
        lambda *a, **kw: _MockResponse(404),
    )
    client = StravaClient(access_token="x")
    assert client.fetch_activity_summary(999) is None


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
