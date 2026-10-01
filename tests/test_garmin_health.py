"""Tests de l'ingestion santé Garmin (module ``ingestion/garmin_health.py``).

Client Garmin entièrement simulé (aucun réseau) : extracteurs, fetch par date,
sync vers ``morning_metrics`` et arbitrage de provider.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from domestique_ai.ingestion.db import get_sync_meta, init_db
from domestique_ai.ingestion.garmin_health import (
    GARMIN_HEALTH_LAST_ERROR_KEY,
    GARMIN_HEALTH_LAST_SYNC_KEY,
    GarminHealthError,
    extract_active_calories,
    extract_body_battery,
    extract_hrv,
    extract_respiration,
    extract_resting_hr,
    extract_sleep,
    extract_sleep_fallbacks,
    extract_spo2,
    extract_steps,
    extract_training_readiness,
    extract_weight,
    fetch_garmin_morning_data,
    sync_garmin_health_morning_metrics,
)
from domestique_ai.processing.morning_metrics import (
    fetch_morning_entry,
    get_health_provider,
    resolve_health_provider,
    save_morning_entry,
    set_health_provider,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


def _sleep_payload(wake_date: str = "2026-05-02") -> dict:
    return {
        "dailySleepDTO": {
            "calendarDate": wake_date,
            "sleepTimeSeconds": 27000,
            "deepSleepSeconds": 5400,
            "remSleepSeconds": 7200,
            "lightSleepSeconds": 14400,
            "awakeSleepSeconds": 1800,
            "avgSleepHRV": 58.0,
            "avgSpO2": 96.5,
            "avgRespirationValue": 13.8,
            "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
        },
        "sleepLevels": [
            {
                "startGMT": "2026-05-01T23:00:00.0",
                "endGMT": "2026-05-02T01:00:00.0",
                "activityLevel": 0.0,
            },
            {
                "startGMT": "2026-05-02T01:00:00.0",
                "endGMT": "2026-05-02T03:00:00.0",
                "activityLevel": 2.0,
            },
            {
                "startGMT": "2026-05-02T03:00:00.0",
                "endGMT": "2026-05-02T05:00:00.0",
                "activityLevel": 1.0,
            },
            {
                "startGMT": "2026-05-02T05:00:00.0",
                "endGMT": "2026-05-02T05:30:00.0",
                "activityLevel": 3.0,
            },
        ],
    }


class _FakeHealthClient:
    """Client garminconnect simulé : payloads par date, appels comptés."""

    def __init__(
        self, stats=None, sleep=None, hrv=None, spo2=None, resp=None, readiness=None, body=None
    ):
        self._stats = stats or {}
        self._sleep = sleep or {}
        self._hrv = hrv or {}
        self._spo2 = spo2 or {}
        self._resp = resp or {}
        self._readiness = readiness or {}
        self._body = body
        self.calls: list[str] = []

    def get_stats(self, date):
        self.calls.append("stats")
        return self._stats.get(date)

    def get_sleep_data(self, date):
        self.calls.append("sleep")
        return self._sleep.get(date)

    def get_hrv_data(self, date):
        self.calls.append("hrv")
        return self._hrv.get(date)

    def get_spo2_data(self, date):
        self.calls.append("spo2")
        return self._spo2.get(date)

    def get_respiration_data(self, date):
        self.calls.append("resp")
        return self._resp.get(date)

    def get_morning_training_readiness(self, date):
        self.calls.append("readiness")
        return self._readiness.get(date)

    def get_body_composition(self, start, end=None):
        self.calls.append("body")
        return self._body


def _full_client() -> _FakeHealthClient:
    return _FakeHealthClient(
        stats={
            "2026-05-02": {
                "restingHeartRate": 48,
                "totalSteps": 8500,
                "activeKilocalories": 420,
                "bodyBatteryLowestValue": 15,
                "bodyBatteryHighestValue": 92,
            }
        },
        sleep={"2026-05-02": _sleep_payload()},
        hrv={"2026-05-02": {"hrvSummary": {"lastNightAvg": 55.0, "weeklyAvg": 52.0}}},
        spo2={"2026-05-02": {"averageSpO2": 96.2}},
        resp={"2026-05-02": {"avgSleepRespirationValue": 13.9}},
        readiness={"2026-05-02": {"score": 78, "inputContext": "AFTER_WAKEUP_RESET"}},
        body={
            "dateWeightList": [
                {"calendarDate": "2026-05-01", "weight": 71000.0},
                {"calendarDate": "2026-05-02", "weight": 70500.0},
            ]
        },
    )


# ---------------------------------------------------------------------------
# Extracteurs
# ---------------------------------------------------------------------------


def test_extract_hrv_variants():
    assert extract_hrv({"hrvSummary": {"lastNightAvg": 55.0}}) == 55.0
    assert extract_hrv({"hrvSummary": {"weeklyAvg": 52.0}}) == 52.0
    assert extract_hrv({"hrvSummary": {}}) is None
    assert extract_hrv(None) is None
    assert extract_hrv({"hrvSummary": "nope"}) is None


def test_extract_stats_fields():
    stats = {
        "restingHeartRate": 48,
        "totalSteps": 8500,
        "activeKilocalories": 420,
        "bodyBatteryLowestValue": 15,
        "bodyBatteryHighestValue": 92,
    }
    assert extract_resting_hr(stats) == 48.0
    assert extract_steps(stats) == 8500
    assert extract_active_calories(stats) == 420
    assert extract_body_battery(stats) == (15, 92)
    assert extract_body_battery(None) == (None, None)


def test_extract_spo2_fallback_chain():
    assert extract_spo2({"averageSpO2": 96.2}) == 96.2
    assert extract_spo2({"lastNightAvg": 95.5}) == 95.5
    assert extract_spo2({"lastSevenDaysAvgSpO2": "94.8"}) == 94.8
    assert extract_spo2({}) is None


def test_extract_respiration_fallback():
    assert extract_respiration({"avgSleepRespirationValue": 13.9}) == 13.9
    assert extract_respiration({"avgWakingRespirationValue": 14.5}) == 14.5
    assert extract_respiration({}) is None


def test_extract_training_readiness_dict_and_list():
    assert extract_training_readiness({"score": 78}) == 78
    assert (
        extract_training_readiness(
            [
                {"score": 40, "inputContext": "MANUAL"},
                {"score": 78, "inputContext": "AFTER_WAKEUP_RESET"},
            ]
        )
        == 78
    )
    assert extract_training_readiness([]) is None
    assert extract_training_readiness(None) is None


def test_extract_sleep_converts_seconds_and_stages():
    result = extract_sleep(_sleep_payload())
    assert result["sleep_hours"] == 7.5
    assert result["sleep_deep_min"] == 90
    assert result["sleep_rem_min"] == 120
    assert result["sleep_light_min"] == 240
    assert result["sleep_awake_min"] == 30
    assert result["garmin_sleep_score"] == 82
    stages = result["sleep_stages"]
    assert [stage["type"] for stage in stages] == ["DEEP", "REM", "LIGHT", "AWAKE"]
    # Les timestamps Garmin sans fuseau sont normalisés en UTC (Z).
    assert stages[0]["start"] == "2026-05-01T23:00:00.0Z"
    assert stages[0]["end"] == "2026-05-02T01:00:00.0Z"


def test_extract_sleep_empty_payload():
    assert extract_sleep(None) == {}
    assert extract_sleep({}) == {}


def test_extract_sleep_fallbacks():
    assert extract_sleep_fallbacks(_sleep_payload()) == {
        "hrv_ms": 58.0,
        "spo2_avg_pct": 96.5,
        "respiratory_rate_avg_bpm": 13.8,
    }
    assert extract_sleep_fallbacks({}) == {}


def test_extract_weight_variants():
    assert (
        extract_weight({"dateWeightList": [{"calendarDate": "2026-05-01", "weight": 71000.0}]})
        == 71.0
    )
    assert (
        extract_weight(
            {
                "dailyWeightSummaries": [
                    {"allWeightMetrics": [{"calendarDate": "2026-05-02", "weight": 70500.0}]}
                ]
            }
        )
        == 70.5
    )
    assert extract_weight({"totalAverage": {"weight": 70000.0}}) == 70.0
    # Aberrant rejeté.
    assert extract_weight({"totalAverage": {"weight": 5000.0}}) is None
    assert extract_weight({}) is None


# ---------------------------------------------------------------------------
# Fetch par date
# ---------------------------------------------------------------------------


def test_fetch_morning_data_keys_by_wake_date():
    data = fetch_garmin_morning_data(_full_client(), dt.date(2026, 5, 1), dt.date(2026, 5, 2))
    assert set(data) == {"2026-05-01", "2026-05-02"}
    day = data["2026-05-02"]
    assert day["hrv_ms"] == 55.0
    assert day["resting_hr"] == 48.0
    assert day["steps"] == 8500
    assert day["active_calories"] == 420
    assert day["spo2_avg_pct"] == 96.2
    assert day["respiratory_rate_avg_bpm"] == 13.9
    assert day["garmin_readiness_score"] == 78
    assert day["garmin_body_battery_min"] == 15
    assert day["garmin_body_battery_max"] == 92
    assert day["sleep_hours"] == 7.5
    assert day["garmin_sleep_score"] == 82
    assert day["weight_kg"] == 70.5
    # Jour sans métrique de récupération : seul le pesage du 1er est rattaché.
    assert data["2026-05-01"]["weight_kg"] == 71.0
    assert all(value is None for key, value in data["2026-05-01"].items() if key != "weight_kg")


def test_fetch_morning_data_sleep_fallbacks():
    client = _FakeHealthClient(
        stats={"2026-05-02": {"restingHeartRate": 48}},
        sleep={"2026-05-02": _sleep_payload()},
        hrv={},
        spo2={},
        resp={},
    )
    day = fetch_garmin_morning_data(client, dt.date(2026, 5, 2), dt.date(2026, 5, 2))["2026-05-02"]
    assert day["hrv_ms"] == 58.0
    assert day["spo2_avg_pct"] == 96.5
    assert day["respiratory_rate_avg_bpm"] == 13.8


def test_fetch_morning_data_raises_when_nothing_returned():
    class _BrokenClient:
        def __getattr__(self, name):
            def _raise(*args, **kwargs):
                raise RuntimeError("boom")

            return _raise

    with pytest.raises(GarminHealthError):
        fetch_garmin_morning_data(_BrokenClient(), dt.date(2026, 5, 2), dt.date(2026, 5, 2))


# ---------------------------------------------------------------------------
# Sync + arbitrage provider
# ---------------------------------------------------------------------------


def test_sync_writes_db_with_source_and_bonus(db_path: Path):
    result = sync_garmin_health_morning_metrics(
        _full_client(),
        start_date=dt.date(2026, 5, 2),
        end_date=dt.date(2026, 5, 2),
        db_path=db_path,
    )
    assert result == {
        "synced_dates": ["2026-05-02"],
        "skipped_dates": [],
        "disabled": False,
    }
    entry = fetch_morning_entry("2026-05-02", db_path=db_path)
    assert entry["source"] == "garmin"
    assert entry["hrv_ms"] == 55.0
    assert entry["sleep_score_computed"] == 1
    assert entry["stress_score_computed"] == 1
    assert entry["garmin_sleep_score"] == 82
    assert entry["garmin_readiness_score"] == 78
    assert entry["garmin_body_battery_min"] == 15
    assert entry["garmin_body_battery_max"] == 92
    assert json.loads(entry["sleep_stages_json"])[0]["type"] == "DEEP"
    assert get_sync_meta(GARMIN_HEALTH_LAST_SYNC_KEY, db_path=db_path) is not None
    assert get_sync_meta(GARMIN_HEALTH_LAST_ERROR_KEY, db_path=db_path) == ""


def test_sync_disabled_when_google_preferred(db_path: Path):
    set_health_provider("google_health", db_path=db_path)
    client = _full_client()
    result = sync_garmin_health_morning_metrics(
        client,
        start_date=dt.date(2026, 5, 2),
        end_date=dt.date(2026, 5, 2),
        db_path=db_path,
    )
    assert result["disabled"] is True
    assert result["synced_dates"] == []
    assert client.calls == []  # aucun appel API
    assert fetch_morning_entry("2026-05-02", db_path=db_path) is None


def test_sync_preserves_manual_scores(db_path: Path):
    save_morning_entry(
        "2026-05-02",
        sleep_score=95,
        sleep_score_computed=0,
        stress_score=88,
        stress_score_computed=0,
        notes="Nuit manuelle",
        db_path=db_path,
    )
    sync_garmin_health_morning_metrics(
        _full_client(),
        start_date=dt.date(2026, 5, 2),
        end_date=dt.date(2026, 5, 2),
        db_path=db_path,
    )
    entry = fetch_morning_entry("2026-05-02", db_path=db_path)
    assert entry["sleep_score"] == 95
    assert entry["sleep_score_computed"] == 0
    assert entry["stress_score"] == 88
    assert entry["stress_score_computed"] == 0
    assert entry["notes"] == "Nuit manuelle"
    assert entry["hrv_ms"] == 55.0


def test_sync_garmin_priority_over_google_in_auto(db_path: Path):
    save_morning_entry(
        "2026-05-02",
        hrv_ms=40.0,
        source="google_health",
        db_path=db_path,
    )
    sync_garmin_health_morning_metrics(
        _full_client(),
        start_date=dt.date(2026, 5, 2),
        end_date=dt.date(2026, 5, 2),
        db_path=db_path,
    )
    entry = fetch_morning_entry("2026-05-02", db_path=db_path)
    assert entry["source"] == "garmin"
    assert entry["hrv_ms"] == 55.0


def test_sync_records_last_error_on_failure(db_path: Path):
    class _BrokenClient:
        def __getattr__(self, name):
            def _raise(*args, **kwargs):
                raise RuntimeError("boom")

            return _raise

    with pytest.raises(GarminHealthError):
        sync_garmin_health_morning_metrics(
            _BrokenClient(),
            start_date=dt.date(2026, 5, 2),
            end_date=dt.date(2026, 5, 2),
            db_path=db_path,
        )
    error = get_sync_meta(GARMIN_HEALTH_LAST_ERROR_KEY, db_path=db_path) or ""
    assert "Garmin" in error


def test_weight_only_day_preserves_existing_metrics(db_path: Path):
    """Un pesage Garmin seul ne doit pas effacer le sommeil Google existant."""
    save_morning_entry(
        "2026-05-02",
        hrv_ms=40.0,
        sleep_hours=6.0,
        source="google_health",
        db_path=db_path,
    )
    client = _FakeHealthClient(
        body={"dateWeightList": [{"calendarDate": "2026-05-02", "weight": 70500.0}]}
    )
    sync_garmin_health_morning_metrics(
        client,
        start_date=dt.date(2026, 5, 2),
        end_date=dt.date(2026, 5, 2),
        db_path=db_path,
    )
    entry = fetch_morning_entry("2026-05-02", db_path=db_path)
    assert entry["weight_kg"] == 70.5
    assert entry["hrv_ms"] == 40.0
    assert entry["sleep_hours"] == 6.0
    # Upsert ciblé : la provenance Google de la ligne n'est pas volée.
    assert entry["source"] == "google_health"


def test_has_auto_metrics_excludes_weight():
    from domestique_ai.processing.morning_metrics import has_auto_metrics, provider_has_data

    assert has_auto_metrics({"hrv_ms": 40.0}) is True
    assert has_auto_metrics({"weight_kg": 70.0}) is False
    assert has_auto_metrics(None) is False
    assert provider_has_data({"weight_kg": 70.0}) is True
    assert provider_has_data({"sleep_stages": [{"type": "DEEP"}]}) is True
    assert provider_has_data({}) is False


# ---------------------------------------------------------------------------
# Préférence de provider
# ---------------------------------------------------------------------------


def test_health_provider_preference_roundtrip(db_path: Path):
    assert get_health_provider(db_path=db_path) == "auto"
    set_health_provider("garmin", db_path=db_path)
    assert get_health_provider(db_path=db_path) == "garmin"
    with pytest.raises(ValueError):
        set_health_provider("strava", db_path=db_path)


def test_resolve_health_provider():
    assert resolve_health_provider("auto", garmin_connected=True, google_connected=True) == "garmin"
    assert (
        resolve_health_provider("auto", garmin_connected=False, google_connected=True)
        == "google_health"
    )
    assert resolve_health_provider("auto", garmin_connected=False, google_connected=False) is None
    assert (
        resolve_health_provider("google_health", garmin_connected=True, google_connected=False)
        is None
    )
    assert (
        resolve_health_provider("garmin", garmin_connected=True, google_connected=False) == "garmin"
    )
