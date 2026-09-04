"""Tests pour l'ingestion Garmin Connect (pas de réseau — client mocké)."""

from __future__ import annotations

import datetime as dt
import sqlite3
from unittest.mock import MagicMock

import pytest

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import init_db
from domestique_ai.ingestion.garmin import (
    GarminIngestError,
    extract_activity_data,
    get_ingest_client,
    map_sport_type,
    parse_details_streams,
    save_garmin_activity,
    sync_activities_garmin,
)


def _ctx(db_path, *, ftp=250.0, hr_rest=None, hr_max=None) -> AthleteContext:
    return AthleteContext(
        db_path=db_path,
        profile_path=db_path.parent / "profile.yaml",
        objective_path=db_path.parent / "objective.yaml",
        availability_path=db_path.parent / "availability.yaml",
        ftp=ftp,
        hr_rest=hr_rest,
        hr_max=hr_max,
        sex="M",
        lthr_pct=0.88,
    )


# ---------------------------------------------------------------------------
# Mapping sport + extraction des résumés
# ---------------------------------------------------------------------------


def test_map_sport_type_known_keys():
    assert map_sport_type("cycling") == "Ride"
    assert map_sport_type("indoor_cycling") == "VirtualRide"
    assert map_sport_type("virtual_ride") == "VirtualRide"
    assert map_sport_type("mountain_biking") == "MountainBikeRide"
    assert map_sport_type("running") == "Run"


def test_map_sport_type_fallback_and_none():
    assert map_sport_type("kayaking") == "Kayaking"
    assert map_sport_type(None) is None
    assert map_sport_type("") is None


def test_extract_activity_data_full_summary():
    raw = {
        "activityId": 18435401234,
        "activityName": "Sortie matinale",
        "startTimeGMT": "2026-08-30T08:12:33.0",
        "duration": 3600.0,
        "averageHR": 142.0,
        "maxHR": 178.0,
        "averagePower": 231.5,
        "elevationGain": 512.0,
        "distance": 62500.0,
        "activityType": {"typeKey": "cycling"},
    }
    data = extract_activity_data(raw)
    assert data is not None
    assert data["id"] == 18435401234
    assert data["date"] == "2026-08-30T08:12:33Z"
    assert data["duration"] == 3600.0
    assert data["avg_heart_rate"] == 142.0
    assert data["max_heart_rate"] == 178.0
    assert data["avg_power"] == 231.5
    assert data["elevation_gain"] == 512.0
    assert data["distance"] == 62500.0
    assert data["sport_type"] == "Ride"


def test_extract_activity_data_without_id_returns_none():
    assert extract_activity_data({"activityName": "x"}) is None
    assert extract_activity_data(None) is None


def test_extract_activity_data_handles_local_time_fallback():
    raw = {
        "activityId": 1,
        "startTimeLocal": "2026-08-30 08:12:33",
        "activityType": {"typeKey": "indoor_cycling"},
    }
    data = extract_activity_data(raw)
    assert data is not None
    assert data["date"] == "2026-08-30T08:12:33Z"
    assert data["sport_type"] == "VirtualRide"


# ---------------------------------------------------------------------------
# Parsing des streams de détails (défensif, 2 orientations)
# ---------------------------------------------------------------------------


def test_parse_details_streams_per_descriptor_orientation():
    details = {
        "activityId": 1,
        "metricsEntries": [
            {
                "metricDescriptorDTOs": [{"key": "seconds"}],
                "metrics": [0, 1, 2, 3],
            },
            {
                "metricDescriptorDTOs": [{"key": "directHeartRate"}],
                "metrics": [95, 120, 140, None],
            },
            {
                "metricDescriptorDTOs": [{"key": "directAirTemperature"}],
                "metrics": [18.5, 19.0, 19.2, 19.1],
            },
        ],
    }
    streams = parse_details_streams(details)
    assert streams is not None
    assert streams["time"] == [0.0, 1.0, 2.0, 3.0]
    assert streams["heartrate"] == [95.0, 120.0, 140.0]
    assert streams["temp"] == [18.5, 19.0, 19.2, 19.1]


def test_parse_details_streams_per_sample_orientation():
    details = {
        "activityId": 2,
        "data": {
            "metricsEntries": [
                {
                    "metrics": [0, 95, 18.5],
                    "metricDescriptorDTOs": [
                        {"key": "seconds"},
                        {"key": "directHeartRate"},
                        {"key": "directAirTemperature"},
                    ],
                },
                {
                    "metrics": [1, 120, 19.0],
                    "metricDescriptorDTOs": [
                        {"key": "seconds"},
                        {"key": "directHeartRate"},
                        {"key": "directAirTemperature"},
                    ],
                },
            ]
        },
    }
    streams = parse_details_streams(details)
    assert streams is not None
    assert streams["heartrate"] == [95.0, 120.0]
    assert streams["time"] == [0.0, 1.0]
    assert streams["temp"] == [18.5, 19.0]


def test_parse_details_streams_garbage_returns_none():
    assert parse_details_streams(None) is None
    assert parse_details_streams({"foo": "bar"}) is None
    assert parse_details_streams({"metricsEntries": [{"metrics": []}]}) is None
    assert parse_details_streams({"metricsEntries": [{"metrics": [1, 2, 3]}]}) is None


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def test_save_garmin_activity_inserts_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    db = tmp_path / "g.db"
    init_db(db)
    activity = {
        "id": 18435401234,
        "date": "2026-08-30T08:12:33Z",
        "duration": 3600,
        "avg_power": 250.0,
        "sport_type": "Ride",
    }
    assert save_garmin_activity(activity, db_path=db) is True
    assert save_garmin_activity(activity, db_path=db) is False

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT strava_id, garmin_id, training_load FROM activities WHERE garmin_id = ?",
            (18435401234,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] is None
    assert row[1] == 18435401234
    assert row[2] is not None  # TSS calculé


def test_save_garmin_activity_writes_zones_and_temp(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    activity = {"id": 42, "date": "2026-08-30T08:12:33Z", "duration": 3600}
    zones = {"z1": 600.0, "z2": 1800.0, "z3": 900.0, "z4": 240.0, "z5": 60.0}
    assert (
        save_garmin_activity(activity, db_path=db, hr_zones=zones, temp_summary=(18.0, 15.0, 21.0))
        is True
    )
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT hr_z1_time, hr_z5_time, avg_temp, max_temp FROM activities WHERE garmin_id = 42"
        ).fetchone()
    finally:
        conn.close()
    assert row == (600.0, 60.0, 18.0, 21.0)


def test_unique_index_blocks_duplicate_garmin_id(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    assert save_garmin_activity({"id": 7, "duration": 600}, db_path=db) is True
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO activities (garmin_id) VALUES (?)", (7,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sync (client mocké)
# ---------------------------------------------------------------------------


def _mock_client(summaries: list[dict], details: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.get_activities_by_date.return_value = summaries
    client.get_activity_details.return_value = details
    return client


def test_sync_activities_garmin_inserts_summaries(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 101,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 3600,
            "averageHR": 140.0,
            "activityType": {"typeKey": "cycling"},
        },
        {"bad": "row"},
    ]
    client = _mock_client(summaries)
    inserted = sync_activities_garmin(
        client, dt.date(2026, 8, 1), dt.date(2026, 8, 31), ctx=_ctx(db)
    )
    assert inserted == 1
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT garmin_id, sport_type FROM activities WHERE garmin_id = 101"
        ).fetchone()
    finally:
        conn.close()
    assert row == (101, "Ride")
    client.get_activity_details.assert_not_called()


def test_sync_activities_garmin_fetches_streams_when_hr_configured(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 202,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 3600,
            "averageHR": 140.0,
        }
    ]
    details = {
        "metricsEntries": [
            {"metricDescriptorDTOs": [{"key": "seconds"}], "metrics": [0, 1, 2]},
            {"metricDescriptorDTOs": [{"key": "directHeartRate"}], "metrics": [100, 130, 150]},
        ]
    }
    client = _mock_client(summaries, details)
    inserted = sync_activities_garmin(
        client,
        dt.date(2026, 8, 1),
        dt.date(2026, 8, 31),
        ctx=_ctx(db, hr_rest=60, hr_max=200),
    )
    assert inserted == 1
    client.get_activity_details.assert_called_once_with(202)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT hr_z1_time, hr_z2_time FROM activities WHERE garmin_id = 202"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is not None
    assert row[1] is not None


def test_sync_activities_garmin_survives_details_failure(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 303,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 600,
            "averageHR": 120.0,
        }
    ]
    client = _mock_client(summaries, None)
    client.get_activity_details.side_effect = RuntimeError("boom")
    inserted = sync_activities_garmin(
        client,
        dt.date(2026, 8, 1),
        dt.date(2026, 8, 31),
        ctx=_ctx(db, hr_rest=60, hr_max=200),
    )
    assert inserted == 1


def test_sync_activities_garmin_unparseable_details_logs_and_continues(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 404,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 600,
            "averageHR": 120.0,
        }
    ]
    client = _mock_client(summaries, {"unexpected": "payload"})
    inserted = sync_activities_garmin(
        client,
        dt.date(2026, 8, 1),
        dt.date(2026, 8, 31),
        ctx=_ctx(db, hr_rest=60, hr_max=200),
    )
    assert inserted == 1


# ---------------------------------------------------------------------------
# Auth / config
# ---------------------------------------------------------------------------


def test_get_ingest_client_raises_without_credentials(monkeypatch, tmp_path):

    monkeypatch.setenv("GARMIN_EMAIL", "")
    monkeypatch.setenv("GARMIN_PASSWORD", "")
    with pytest.raises(GarminIngestError):
        get_ingest_client(token_dir=tmp_path)


def test_sync_activities_garmin_incremental_window(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    db = tmp_path / "g.db"
    init_db(db)
    save_garmin_activity({"id": 999, "date": "2026-08-20T10:00:00Z"}, db_path=db)
    client = _mock_client([])
    sync_activities_garmin(client, ctx=_ctx(db))
    args = client.get_activities_by_date.call_args
    start, end = args[0]
    assert start == "2026-08-19"
    assert end == (dt.date.today() + dt.timedelta(days=1)).isoformat()
