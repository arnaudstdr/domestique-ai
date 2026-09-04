"""Tests des endpoints streams + weather des activités (client Garmin mocké)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db
from domestique_ai.ingestion.garmin import GarminIngestError

# Payload moderne (shape réelle 09/2026) — layout 13 slots identique au
# fixture _modern_details de test_garmin.py.
_MODERN_DETAILS = {
    "metricDescriptors": [
        {"metricsIndex": i, "key": k}
        for i, k in enumerate(
            (
                "sumMovingDuration",
                "sumDistance",
                "directUncorrectedElevation",
                "directTimestamp",
                "directAirTemperature",
                "sumElapsedDuration",
                "directLongitude",
                "directLatitude",
                "directSpeed",
                "directElevation",
                "directHeartRate",
                "sumDuration",
                "directVerticalSpeed",
            )
        )
    ],
    "activityDetailMetrics": [
        {
            "metrics": [
                0.0,
                2.31,
                175.6,
                1788363203000.0,
                21.0,
                0.0,
                7.4457,
                48.2542,
                2.322,
                175.6,
                141.0,
                0.0,
                None,
            ]
        },
        {
            "metrics": [
                5.0,
                20.0,
                176.0,
                1788363205000.0,
                21.5,
                5.0,
                7.4458,
                48.2543,
                3.1,
                176.0,
                150.0,
                5.0,
                0.2,
            ]
        },
        {
            "metrics": [
                10.0,
                40.0,
                177.0,
                1788363208000.0,
                22.0,
                10.0,
                7.4460,
                48.2550,
                4.0,
                177.0,
                158.0,
                10.0,
                0.5,
            ]
        },
    ],
    "geoPolylineDTO": {
        "polyline": [
            {"lat": 48.2542, "lon": 7.4457},
            {"lat": 48.2543, "lon": 7.4458},
        ]
    },
}

_WEATHER_RAW = {
    "issueDate": "2026-09-02T15:30:00.000+00:00",
    "temp": 82,  # °F → 27.8 °C
    "apparentTemp": 82,
    "dewPoint": 52,
    "relativeHumidity": 35,
    "windDirection": 0,
    "windDirectionCompassPoint": "n",
    "weatherStationDTO": {"id": "LFGA", "name": ""},
    "weatherTypeDTO": {"desc": "Fair"},
}


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "api_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    init_db(db)
    from domestique_ai.api.main import app
    from domestique_ai.api.routers import activities as activities_router

    # Cache streams module-level : purge entre les tests.
    activities_router._streams_cache.clear()

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def _insert_garmin_activity(db: Path, garmin_id: int, date: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (garmin_id, date, duration, distance) VALUES (?, ?, ?, ?)",
            (garmin_id, date, 3600, 36616.0),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_strava_activity(db: Path, strava_id: int, date: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration) VALUES (?, ?, ?)",
            (strava_id, date, 3600),
        )
        conn.commit()
    finally:
        conn.close()


def _mock_garmin_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    from domestique_ai.api.routers import activities as activities_router

    fake = MagicMock()
    fake.get_activity_details.return_value = _MODERN_DETAILS
    fake.get_activity_weather.return_value = _WEATHER_RAW
    monkeypatch.setattr(activities_router, "get_ingest_client", lambda **kw: fake)
    return fake


def test_streams_returns_parsed_series(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _insert_garmin_activity(tmp_path / "api_test.db", 24212850226, "2026-09-02T15:33:23Z")
    _mock_garmin_client(monkeypatch)

    r = client.get("/api/activities/24212850226/streams")
    assert r.status_code == 200
    body = r.json()
    assert body["heartrate"] == [141.0, 150.0, 158.0]
    assert body["time"] == [0, 5, 10]
    assert body["temp"] == [21.0, 21.5, 22.0]
    assert body["watts"] is None  # pas de canal puissance dans le payload
    assert body["latlng"] == [
        [48.2542, 7.4457],
        [48.2543, 7.4458],
        [48.2550, 7.4460],
    ]


def test_streams_cached_on_second_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _insert_garmin_activity(tmp_path / "api_test.db", 202, "2026-09-02T15:33:23Z")
    fake = _mock_garmin_client(monkeypatch)

    assert client.get("/api/activities/202/streams").status_code == 200
    assert client.get("/api/activities/202/streams").status_code == 200
    # 1 seul appel Garmin : 2e réponse servie depuis le cache mémoire 1 h.
    assert fake.get_activity_details.call_count == 1


def test_streams_404_for_strava_legacy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _insert_strava_activity(tmp_path / "api_test.db", 12345, "2025-06-01T08:00:00Z")
    _mock_garmin_client(monkeypatch)

    r = client.get("/api/activities/12345/streams")
    assert r.status_code == 404
    assert "Strava" in r.json()["detail"]


def test_streams_503_when_garmin_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from domestique_ai.api.routers import activities as activities_router

    _insert_garmin_activity(tmp_path / "api_test.db", 202, "2026-09-02T15:33:23Z")

    def _raise(**kw):  # noqa: ANN001, ANN003
        raise GarminIngestError("Pas de tokens Garmin Connect")

    monkeypatch.setattr(activities_router, "get_ingest_client", _raise)
    r = client.get("/api/activities/202/streams")
    assert r.status_code == 503
    assert "tokens" in r.json()["detail"]


def test_streams_404_unknown_activity(client: TestClient) -> None:
    r = client.get("/api/activities/999999/streams")
    assert r.status_code == 404


def test_weather_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _insert_garmin_activity(tmp_path / "api_test.db", 202, "2026-09-02T15:33:23Z")
    _mock_garmin_client(monkeypatch)

    r = client.get("/api/activities/202/weather")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["temp_c"] == 27.8  # 82 °F
    assert body["apparent_temp_c"] == 27.8
    assert body["relative_humidity_pct"] == 35
    assert body["wind_compass"] == "n"
    assert body["description"] == "Fair"
    assert body["station"] == "LFGA"


def test_weather_best_effort_on_garmin_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from domestique_ai.api.routers import activities as activities_router

    _insert_garmin_activity(tmp_path / "api_test.db", 202, "2026-09-02T15:33:23Z")
    fake = MagicMock()
    fake.get_activity_weather.side_effect = RuntimeError("boom")
    monkeypatch.setattr(activities_router, "get_ingest_client", lambda **kw: fake)

    r = client.get("/api/activities/202/weather")
    assert r.status_code == 200  # best-effort : jamais de 5xx
    assert r.json()["available"] is False


def test_weather_404_for_strava_legacy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _insert_strava_activity(tmp_path / "api_test.db", 12345, "2025-06-01T08:00:00Z")
    _mock_garmin_client(monkeypatch)

    r = client.get("/api/activities/12345/weather")
    assert r.status_code == 404
