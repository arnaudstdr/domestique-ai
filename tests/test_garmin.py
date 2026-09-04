"""Tests pour l'ingestion Garmin Connect (pas de réseau — client mocké)."""

from __future__ import annotations

import datetime as dt
import sqlite3
from unittest.mock import MagicMock

import pytest

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import get_sync_meta, init_db, set_sync_meta
from domestique_ai.ingestion.garmin import (
    BACKFILL_FLAG,
    GarminIngestError,
    backfill_garmin_fields,
    encode_polyline,
    extract_activity_data,
    get_ingest_client,
    map_sport_type,
    parse_details_series,
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
    assert map_sport_type("road_biking") == "Ride"
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


def test_extract_activity_data_enriched_fields():
    raw = {
        "activityId": 24212850226,
        "activityName": "Séléstat Cyclisme sur route",
        "startTimeGMT": "2026-09-02T15:33:23.0",
        "duration": 5035.77,
        "averageHR": 161.0,
        "maxHR": 188.0,
        "averagePower": 231.0,
        "maxPower": 842.0,
        "elevationGain": 15.0,
        "elevationLoss": 14.0,
        "distance": 36616.18,
        "calories": 1021.0,
        "averageSpeed": 7.271,
        "maxSpeed": 10.684,
        "startLatitude": 48.2542,
        "startLongitude": 7.4457,
        "averageBikingCadenceInRevPerMinute": 85.0,
        "maxBikingCadenceInRpm": 102.0,
        "activityType": {"typeKey": "road_biking"},
    }
    data = extract_activity_data(raw)
    assert data is not None
    assert data["name"] == "Séléstat Cyclisme sur route"
    assert data["calories"] == 1021.0
    assert data["max_power"] == 842.0
    assert data["cadence_avg"] == 85.0
    assert data["cadence_max"] == 102.0
    assert data["speed_avg"] == 7.271
    assert data["speed_max"] == 10.684
    assert data["elevation_loss"] == 14.0
    assert data["start_lat"] == 48.2542
    assert data["start_lng"] == 7.4457


def test_extract_activity_data_cadence_fallback_running():
    raw = {
        "activityId": 2,
        "averageRunningCadenceInStepsPerMinute": 178.0,
        "maxRunningCadenceInStepsPerMinute": 190.0,
    }
    data = extract_activity_data(raw)
    assert data is not None
    assert data["cadence_avg"] == 178.0
    assert data["cadence_max"] == 190.0


def test_extract_activity_data_missing_enriched_fields_are_none():
    data = extract_activity_data({"activityId": 3})
    assert data is not None
    for key in (
        "name",
        "calories",
        "max_power",
        "cadence_avg",
        "cadence_max",
        "speed_avg",
        "speed_max",
        "elevation_loss",
        "start_lat",
        "start_lng",
    ):
        assert data[key] is None


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


# Payload moderne (shape réelle observée 09/2026 sur un Edge) : descriptors
# top-level avec ``metricsIndex`` + un échantillon par entrée.
_MODERN_DESCRIPTOR_KEYS = (
    (0, "sumMovingDuration"),
    (1, "sumDistance"),
    (2, "directUncorrectedElevation"),
    (3, "directTimestamp"),
    (4, "directAirTemperature"),
    (5, "sumElapsedDuration"),
    (6, "directLongitude"),
    (7, "directLatitude"),
    (8, "directSpeed"),
    (9, "directElevation"),
    (10, "directHeartRate"),
    (11, "sumDuration"),
    (12, "directVerticalSpeed"),
)


def _modern_details() -> dict:
    desc = [{"metricsIndex": i, "key": k} for i, k in _MODERN_DESCRIPTOR_KEYS]
    samples = [
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
    ]
    return {
        "metricDescriptors": desc,
        "activityDetailMetrics": samples,
        "geoPolylineDTO": {
            "polyline": [
                {"lat": 48.2542, "lon": 7.4457},
                {"lat": 48.2543, "lon": 7.4458},
                {"lat": 48.2550, "lon": 7.4460},
            ]
        },
    }


def test_parse_details_series_modern_shape():
    series = parse_details_series(_modern_details())
    assert series["heartrate"] == [141.0, 150.0, 158.0]
    # sumElapsedDuration (secondes) prioritaire sur directTimestamp (epoch ms,
    # fallback uniquement si aucune durée n'est fournie).
    assert series["time"] == [0.0, 5.0, 10.0]
    assert series["temp"] == [21.0, 21.5, 22.0]
    assert series["speed"] == [2.322, 3.1, 4.0]
    assert series["distance"] == [2.31, 20.0, 40.0]
    assert series["altitude"] == [175.6, 176.0, 177.0]
    assert series["latlng"] == [
        [48.2542, 7.4457],
        [48.2543, 7.4458],
        [48.2550, 7.4460],
    ]


def test_parse_details_streams_modern_shape():
    streams = parse_details_streams(_modern_details())
    assert streams is not None
    assert streams["heartrate"] == [141.0, 150.0, 158.0]
    assert streams["time"] == [0.0, 5.0, 10.0]
    assert streams["temp"] == [21.0, 21.5, 22.0]


def test_encode_polyline_matches_reference():
    # Exemple de référence du format encodé Google/Strava.
    points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode_polyline(points) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_encode_polyline_needs_two_points():
    assert encode_polyline([]) is None
    assert encode_polyline([(48.0, 7.0)]) is None


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


def test_init_db_normalizes_legacy_road_biking(tmp_path):
    """Les lignes héritées du fallback title-case (typeKey ``road_biking`` non
    mappé → ``RoadBiking``) sont normalisées en ``Ride`` par ``init_db``."""
    db = tmp_path / "g.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO activities (garmin_id, sport_type) VALUES (1, 'RoadBiking')")
        conn.execute("INSERT INTO activities (garmin_id, sport_type) VALUES (2, 'VirtualRide')")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    init_db(db)  # idempotent : un 2e passage ne casse rien.

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT sport_type FROM activities ORDER BY garmin_id").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["Ride", "VirtualRide"]


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
    set_sync_meta(BACKFILL_FLAG, "2026-01-01", db)  # backfill one-off déjà fait
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
    set_sync_meta(BACKFILL_FLAG, "2026-01-01", db)  # backfill one-off déjà fait
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
# Backfill one-off des champs enrichis
# ---------------------------------------------------------------------------


def _backfill_list_payload() -> dict:
    return {
        "activityId": 202,
        "activityName": "Séléstat Cyclisme sur route",
        "startTimeGMT": "2026-08-30T08:12:33.0",
        "duration": 5035.77,
        "averageHR": 161.0,
        "maxHR": 188.0,
        "distance": 36616.18,
        "calories": 1021.0,
        "averageSpeed": 7.271,
        "maxSpeed": 10.684,
        "elevationGain": 15.0,
        "elevationLoss": 14.0,
        "startLatitude": 48.2542,
        "startLongitude": 7.4457,
        "hasPolyline": True,
        "activityType": {"typeKey": "road_biking"},
    }


def test_backfill_garmin_fields_updates_existing_row(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    # Ligne existante d'avant le déploiement : zones/temp/polyline à NULL.
    save_garmin_activity(
        {
            "id": 202,
            "date": "2026-08-30T08:12:33Z",
            "duration": 5036,
            "avg_heart_rate": 141.0,
            "distance": 36616.0,
        },
        db_path=db,
    )
    client = MagicMock()
    client.get_activities_by_date.return_value = [_backfill_list_payload()]
    client.get_activity_details.return_value = _modern_details()

    stats = backfill_garmin_fields(client, db, ctx=_ctx(db, hr_rest=60, hr_max=200))

    assert stats["updated"] == 1
    assert stats["polylines"] == 1
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT name, calories, speed_avg, elevation_loss, start_lat, start_lng, "
            "map_polyline, hr_z1_time, avg_temp, min_temp, max_temp "
            "FROM activities WHERE garmin_id = 202"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "Séléstat Cyclisme sur route"
    assert row[1] == 1021.0
    assert row[2] == 7.271
    assert row[3] == 14.0
    assert row[4] == 48.2542
    assert row[5] == 7.4457
    # Polyline encodée depuis geoPolylineDTO (≤ 2 points ici).
    assert row[6] == encode_polyline([(48.2542, 7.4457), (48.2543, 7.4458), (48.2550, 7.4460)])
    # Zones HR rattrapées depuis les streams + températures.
    assert row[7] is not None
    assert row[8] == 21.5
    assert row[9] == 21.0
    assert row[10] == 22.0


def test_backfill_skips_rows_not_in_db(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    client = MagicMock()
    client.get_activities_by_date.return_value = [_backfill_list_payload()]
    stats = backfill_garmin_fields(client, db, ctx=_ctx(db))
    assert stats == {"updated": 0, "polylines": 0, "details": 0}
    client.get_activity_details.assert_not_called()


def test_backfill_survives_details_failure(tmp_path):
    db = tmp_path / "g.db"
    init_db(db)
    save_garmin_activity(
        {"id": 202, "date": "2026-08-30T08:12:33Z", "duration": 600, "avg_heart_rate": 141.0},
        db_path=db,
    )
    client = MagicMock()
    client.get_activities_by_date.return_value = [_backfill_list_payload()]
    client.get_activity_details.side_effect = RuntimeError("boom")

    stats = backfill_garmin_fields(client, db, ctx=_ctx(db, hr_rest=60, hr_max=200))

    assert stats["updated"] == 1
    assert stats["details"] == 0
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT name, map_polyline FROM activities WHERE garmin_id = 202"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "Séléstat Cyclisme sur route"
    assert row[1] is None  # pas de tracé sans détails


def test_sync_triggers_backfill_when_flag_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 101,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 3600,
            "activityType": {"typeKey": "cycling"},
        }
    ]
    client = _mock_client(summaries)
    sync_activities_garmin(client, dt.date(2026, 8, 1), dt.date(2026, 8, 31), ctx=_ctx(db))
    # Sync + backfill : 2 appels liste, flag posé.
    assert client.get_activities_by_date.call_count == 2
    assert get_sync_meta(BACKFILL_FLAG, db) is not None
    # Sync suivant : flag présent → pas de re-run (1 seul appel de plus).
    sync_activities_garmin(client, dt.date(2026, 9, 1), dt.date(2026, 9, 2), ctx=_ctx(db))
    assert client.get_activities_by_date.call_count == 3


def test_sync_backfill_failure_does_not_block_and_retries(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    db = tmp_path / "g.db"
    init_db(db)
    summaries = [
        {
            "activityId": 101,
            "startTimeGMT": "2026-08-30T08:12:33.0",
            "duration": 3600,
            "activityType": {"typeKey": "cycling"},
        }
    ]
    client = _mock_client(summaries)
    # 1er appel (sync) OK, 2e appel (backfill) KO.
    client.get_activities_by_date.side_effect = [summaries, RuntimeError("boom")]
    inserted = sync_activities_garmin(
        client, dt.date(2026, 8, 1), dt.date(2026, 8, 31), ctx=_ctx(db)
    )
    assert inserted == 1  # la sync n'est pas impactée
    assert get_sync_meta(BACKFILL_FLAG, db) is None  # flag non posé → retry au prochain sync


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
    set_sync_meta(BACKFILL_FLAG, "2026-01-01", db)  # backfill one-off déjà fait
    save_garmin_activity({"id": 999, "date": "2026-08-20T10:00:00Z"}, db_path=db)
    client = _mock_client([])
    sync_activities_garmin(client, ctx=_ctx(db))
    args = client.get_activities_by_date.call_args
    start, end = args[0]
    assert start == "2026-08-19"
    assert end == (dt.date.today() + dt.timedelta(days=1)).isoformat()
