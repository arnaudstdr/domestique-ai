"""Tests unitaires pour le client Strava et la persistance SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domestique_ai.ingestion.strava import (
    StravaClient,
    _last_activity_timestamp,
    backfill_activity_fields,
    backfill_sport_types,
    backfill_temperature,
    init_db,
    save_activity,
    snapshot_athlete_weight,
    summarize_temp_stream,
    sync_activities,
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
        "sport_type": "Ride",
        "type": "Ride",
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
        "sport_type": "Ride",
    }


def test_extract_activity_data_falls_back_to_legacy_type():
    """Si `sport_type` est absent, on retombe sur le champ legacy `type`."""
    client = StravaClient(access_token="x")
    extracted = client.extract_activity_data({
        "id": 1, "start_date": "2025-04-01T08:00:00Z",
        "elapsed_time": 0, "type": "Walk",
    })
    assert extracted["sport_type"] == "Walk"


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


def test_save_activity_persists_sport_type(db_path: Path):
    """`save_activity` doit écrire le champ sport_type en base."""
    save_activity({
        "id": 42, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 145.0, "max_heart_rate": 182.0,
        "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
        "sport_type": "MountainBikeRide",
    }, db_path=db_path, ftp=250.0)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sport_type FROM activities WHERE strava_id = 42"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("MountainBikeRide",)


def test_backfill_sport_types_fills_missing(db_path: Path):
    """Une activité sans sport_type doit être complétée par le backfill."""
    # save_activity sans sport_type → la colonne reste NULL
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
        "sport_type": "Ride",
    }])
    assert backfill_sport_types(client, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sport_type FROM activities WHERE strava_id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("Ride",)


def test_backfill_sport_types_is_idempotent(db_path: Path):
    """Si toutes les activités ont déjà un sport_type, le backfill ne fait rien
    (pas d'appel Strava : `_StubClient` lèverait si on l'appelait à vide)."""
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 150.0, "max_heart_rate": 185.0,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
        "sport_type": "Ride",
    }, db_path=db_path, ftp=250.0)

    class _NoFetchClient(_StubClient):
        def fetch_activities(self, *args, **kwargs):
            raise AssertionError("fetch_activities ne doit pas être appelé")

    assert backfill_sport_types(_NoFetchClient([]), db_path=db_path) == 0


def test_backfill_sport_types_does_not_overwrite_existing(db_path: Path):
    """Une activité ayant déjà un sport_type ne doit pas être réécrite."""
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 150.0, "max_heart_rate": 185.0,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
        "sport_type": "Ride",
    }, db_path=db_path, ftp=250.0)
    save_activity({
        "id": 2, "date": "2025-04-02T08:00:00Z", "duration": 3600,
        "avg_heart_rate": 140.0, "max_heart_rate": 175.0,
        "avg_power": 180.0, "elevation_gain": 0, "distance": 25000,
    }, db_path=db_path, ftp=250.0)

    client = _StubClient([
        # Si Strava renvoyait par erreur "Walk" pour l'activité 1, on ne
        # doit pas écraser sa valeur "Ride" existante.
        {"id": 1, "start_date": "2025-04-01T08:00:00Z",
         "elapsed_time": 3600, "sport_type": "Walk"},
        {"id": 2, "start_date": "2025-04-02T08:00:00Z",
         "elapsed_time": 3600, "sport_type": "Run"},
    ])
    assert backfill_sport_types(client, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT strava_id, sport_type FROM activities ORDER BY strava_id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, "Ride"), (2, "Run")]


def test_summarize_temp_stream_avg_min_max():
    assert summarize_temp_stream([10.0, 20.0, 30.0]) == (20.0, 10.0, 30.0)


def test_summarize_temp_stream_empty_or_none():
    assert summarize_temp_stream(None) is None
    assert summarize_temp_stream([]) is None


def test_summarize_temp_stream_filters_outliers():
    # 999 et None doivent être écartés ; les valeurs restantes 20 et 22 → avg 21.
    assert summarize_temp_stream([20.0, None, 999.0, 22.0]) == (21.0, 20.0, 22.0)


def test_summarize_temp_stream_accepts_subzero():
    # Météo hivernale : -5°C est légitime, doit être pris en compte.
    avg, mn, mx = summarize_temp_stream([-5.0, 0.0, 5.0])
    assert (mn, mx) == (-5.0, 5.0)
    assert avg == pytest.approx(0.0)


def test_save_activity_persists_temp(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity(
        {
            "id": 777, "date": "2025-07-15T08:00:00Z", "duration": 3600,
            "avg_heart_rate": 145.0, "max_heart_rate": 182.0,
            "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
        },
        db_path=db_path, ftp=250.0,
        temp_summary=(28.5, 22.0, 34.0),
    )
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT avg_temp, min_temp, max_temp FROM activities WHERE strava_id = 777"
        ).fetchone()
    finally:
        conn.close()
    assert row == (pytest.approx(28.5), pytest.approx(22.0), pytest.approx(34.0))


def test_save_activity_leaves_temp_null_by_default(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity(
        {
            "id": 778, "date": "2025-07-15T08:00:00Z", "duration": 3600,
            "avg_heart_rate": None, "max_heart_rate": None,
            "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
        },
        db_path=db_path, ftp=250.0,
    )
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT avg_temp, min_temp, max_temp FROM activities WHERE strava_id = 778"
        ).fetchone()
    finally:
        conn.close()
    assert row == (None, None, None)


class _TempStreamsClient(StravaClient):
    """StravaClient minimal qui retourne un stream temp fixé par strava_id."""

    def __init__(self, streams_by_id: dict[int, dict[str, list]]):
        super().__init__(access_token="stub")
        self._streams = streams_by_id

    def fetch_streams_full(self, activity_id, keys):  # type: ignore[override]
        return self._streams.get(activity_id)


def test_backfill_temperature_fills_missing_only(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    # Activité 1 : pas de temp en base → doit être enrichie.
    save_activity({
        "id": 1, "date": "2025-07-15T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)
    # Activité 2 : déjà ventilée → le backfill doit la laisser intacte.
    save_activity({
        "id": 2, "date": "2025-07-16T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0,
        temp_summary=(10.0, 8.0, 12.0))

    client = _TempStreamsClient({
        1: {"temp": [20.0, 25.0, 30.0]},
        # 2 ne devrait jamais être appelée — pas pertinent.
    })
    assert backfill_temperature(client, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT strava_id, avg_temp, min_temp, max_temp "
            "FROM activities ORDER BY strava_id"
        ).fetchall()
    finally:
        conn.close()
    assert rows[0] == (1, pytest.approx(25.0), pytest.approx(20.0), pytest.approx(30.0))
    assert rows[1] == (2, pytest.approx(10.0), pytest.approx(8.0), pytest.approx(12.0))


def test_backfill_temperature_idempotent(db_path: Path):
    """Sans activité à enrichir, le backfill ne fait aucun appel."""
    # Aucune activité du tout → 0 ligne mise à jour, pas de crash.
    client = _TempStreamsClient({})
    assert backfill_temperature(client, db_path=db_path) == 0


def test_backfill_temperature_skips_activities_without_stream(db_path: Path, monkeypatch):
    """Si Strava ne renvoie pas de stream temp, on saute sans toucher la ligne."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity({
        "id": 99, "date": "2025-12-15T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 220.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)
    # Home trainer typiquement : pas de capteur température → streams None.
    client = _TempStreamsClient({99: None})  # type: ignore[arg-type]
    assert backfill_temperature(client, db_path=db_path) == 0


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


# ---- Sync incrémentale (after auto-dérivé) ----------------------------------


def test_last_activity_timestamp_none_when_db_empty(db_path: Path):
    assert _last_activity_timestamp(db_path=db_path) is None


def test_last_activity_timestamp_uses_max_date_minus_1h(
    db_path: Path, monkeypatch
):
    """Retourne MAX(date) - 1h en epoch UTC."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)
    save_activity({
        "id": 2, "date": "2025-04-05T18:30:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)
    save_activity({
        "id": 3, "date": "2025-04-03T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)

    # 2025-04-05T18:30:00Z - 1h = 2025-04-05T17:30:00Z
    import datetime as dt
    expected = int(
        dt.datetime(2025, 4, 5, 17, 30, tzinfo=dt.timezone.utc).timestamp()
    )
    assert _last_activity_timestamp(db_path=db_path) == expected


def test_last_activity_timestamp_handles_invalid_date(db_path: Path):
    """Une date mal formée en base ne doit pas faire crasher la fonction."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date) VALUES (?, ?)",
            (1, "pas-une-date"),
        )
        conn.commit()
    finally:
        conn.close()
    assert _last_activity_timestamp(db_path=db_path) is None


class _SyncStubClient(StravaClient):
    """Capture l'argument ``after`` passé à fetch_activities."""

    def __init__(self):
        super().__init__(access_token="stub")
        self.received_after: int | None = -1  # sentinelle "non appelé"
        self.fetch_calls = 0

    def fetch_activities(self, after=None, per_page=200):  # type: ignore[override]
        self.received_after = after
        self.fetch_calls += 1
        return []

    def fetch_athlete(self):  # type: ignore[override]
        return {"weight": 0}  # snapshot_athlete_weight ne fait rien


def test_sync_activities_uses_last_timestamp_when_after_omitted(
    db_path: Path, monkeypatch
):
    """Sur DB peuplée, sync_activities sans after doit dériver le timestamp."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)

    client = _SyncStubClient()
    sync_activities(client)
    assert client.fetch_calls == 1
    assert client.received_after is not None
    # 2025-04-01T08:00:00Z - 1h = 2025-04-01T07:00:00Z = epoch 1743490800
    import datetime as dt
    expected = int(
        dt.datetime(2025, 4, 1, 7, 0, tzinfo=dt.timezone.utc).timestamp()
    )
    assert client.received_after == expected


def test_sync_activities_empty_db_passes_none(db_path: Path, monkeypatch):
    """Sur DB vide, sync_activities doit demander tout l'historique (after=None)."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)

    client = _SyncStubClient()
    sync_activities(client)
    assert client.received_after is None


def test_sync_activities_explicit_after_overrides_auto(
    db_path: Path, monkeypatch
):
    """Un ``after`` explicite (ex. 0) doit bypasser la dérivation automatique."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    save_activity({
        "id": 1, "date": "2025-04-01T08:00:00Z", "duration": 3600,
        "avg_heart_rate": None, "max_heart_rate": None,
        "avg_power": 200.0, "elevation_gain": 0, "distance": 30000,
    }, db_path=db_path, ftp=250.0)

    client = _SyncStubClient()
    sync_activities(client, after=0)
    assert client.received_after == 0
