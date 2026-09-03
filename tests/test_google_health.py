"""Tests pour l'intégration Google Health API."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domestique_ai.ingestion.google_health import (
    DATA_TYPE_DAILY_HRV,
    DATA_TYPE_DAILY_RHR,
    DATA_TYPE_SLEEP,
    GoogleHealthClient,
    _summarize_sleep_sessions,
    sync_google_health_morning_metrics,
)


@pytest.fixture()
def client(tmp_path: Path) -> GoogleHealthClient:
    tokens_path = tmp_path / "tokens.json"
    return GoogleHealthClient(
        tokens={
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "expires_in": 3600,
        },
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost/callback",
        tokens_path=tokens_path,
    )


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.text = json.dumps(json_data)
    return mock


def test_client_from_tokens_file_missing_returns_none(tmp_path: Path):
    with (
        patch(
            "domestique_ai.ingestion.google_health.get_google_health_tokens_path",
            return_value=tmp_path / "missing.json",
        ),
        patch(
            "domestique_ai.ingestion.google_health.get_google_health_credentials",
            return_value=("cid", "secret", "http://localhost/callback"),
        ),
    ):
        assert GoogleHealthClient.from_tokens_file() is None


def test_get_auth_url_contains_required_params(client: GoogleHealthClient):
    url = client.get_auth_url(state="abc")
    assert "client_id=client-id" in url
    assert "response_type=code" in url
    assert "access_type=offline" in url
    assert "state=abc" in url
    assert "googlehealth.sleep.readonly" in url


def test_exchange_code_saves_tokens(client: GoogleHealthClient):
    token_response = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
    }
    with patch("requests.post", return_value=_mock_response(token_response)) as mock_post:
        result = client.exchange_code("code-xyz")

    mock_post.assert_called_once()
    assert result["access_token"] == "new-access"
    assert client.tokens_path.exists()
    saved = json.loads(client.tokens_path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "new-access"


def test_fetch_morning_data_maps_values(client: GoogleHealthClient):
    def side_effect(method, url, **kwargs):
        if DATA_TYPE_DAILY_HRV in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "date": "2026-05-10",
                            "value": {"averageHeartRateVariabilityMilliseconds": 62.0},
                        }
                    ]
                }
            )
        if DATA_TYPE_DAILY_RHR in url:
            return _mock_response(
                {"dataPoints": [{"date": "2026-05-10", "value": {"beatsPerMinute": 47}}]}
            )
        if "steps" in url:
            return _mock_response(
                {"dataPoints": [{"date": "2026-05-10", "value": {"count": 12345}}]}
            )
        if DATA_TYPE_SLEEP in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "startTime": "2026-05-10T22:00:00Z",
                            "endTime": "2026-05-11T06:00:00Z",
                            "value": {
                                "stages": [
                                    {"stage": "deep", "seconds": 5400},
                                    {"stage": "rem", "seconds": 7200},
                                    {"stage": "light", "seconds": 14400},
                                    {"stage": "awake", "seconds": 1800},
                                ]
                            },
                        }
                    ]
                }
            )
        # Réponses par défaut pour les autres data types.
        return _mock_response({"dataPoints": []})

    import datetime as dt

    with patch("requests.request", side_effect=side_effect):
        data = client.fetch_morning_data(
            dt.date(2026, 5, 10),
            dt.date(2026, 5, 11),
        )

    day = data["2026-05-11"]
    assert day["hrv_ms"] is None
    assert day["resting_hr"] is None
    assert day["steps"] is None
    assert day["sleep_hours"] == 7.5
    assert day["sleep_deep_min"] == 90
    assert day["sleep_rem_min"] == 120
    assert day["sleep_light_min"] == 240
    assert day["sleep_awake_min"] == 30


def test_summarize_sleep_sessions_no_data():
    assert _summarize_sleep_sessions(None) == {
        "sleep_hours": None,
        "sleep_deep_min": None,
        "sleep_rem_min": None,
        "sleep_light_min": None,
        "sleep_awake_min": None,
    }


def test_sync_google_health_morning_metrics_writes_db(client: GoogleHealthClient, tmp_path: Path):
    from domestique_ai.ingestion.strava import init_db

    db_path = tmp_path / "test.db"
    init_db(db_path)

    import datetime as dt

    def side_effect(method, url, **kwargs):
        if DATA_TYPE_DAILY_HRV in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "date": "2026-05-10",
                            "value": {"averageHeartRateVariabilityMilliseconds": 60.0},
                        }
                    ]
                }
            )
        if DATA_TYPE_DAILY_RHR in url:
            return _mock_response(
                {"dataPoints": [{"date": "2026-05-10", "value": {"beatsPerMinute": 48}}]}
            )
        if DATA_TYPE_SLEEP in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "startTime": "2026-05-09T22:00:00Z",
                            "endTime": "2026-05-10T06:00:00Z",
                            "value": {"stages": []},
                        }
                    ]
                }
            )
        return _mock_response({"dataPoints": []})

    with patch("requests.request", side_effect=side_effect):
        result = sync_google_health_morning_metrics(
            client,
            start_date=dt.date(2026, 5, 10),
            end_date=dt.date(2026, 5, 10),
            db_path=db_path,
        )

    assert result["synced_dates"] == ["2026-05-10"]
    from domestique_ai.processing.morning_metrics import fetch_morning_entry

    entry = fetch_morning_entry("2026-05-10", db_path=db_path)
    assert entry["hrv_ms"] == 60.0
    assert entry["resting_hr"] == 48.0
    assert entry["sleep_hours"] == 8.0
    assert entry["readiness_score"] is not None
    assert entry["sleep_score_computed"] == 1


def test_sync_respects_manual_sleep_score(client: GoogleHealthClient, tmp_path: Path):
    from domestique_ai.ingestion.strava import init_db
    from domestique_ai.processing.morning_metrics import save_morning_entry

    db_path = tmp_path / "test.db"
    init_db(db_path)
    save_morning_entry(
        "2026-05-10",
        sleep_score=95,
        sleep_score_computed=0,
        db_path=db_path,
    )

    import datetime as dt

    def side_effect(method, url, **kwargs):
        if DATA_TYPE_DAILY_HRV in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "date": "2026-05-10",
                            "value": {"averageHeartRateVariabilityMilliseconds": 60.0},
                        }
                    ]
                }
            )
        if DATA_TYPE_SLEEP in url:
            return _mock_response(
                {
                    "dataPoints": [
                        {
                            "startTime": "2026-05-09T22:00:00Z",
                            "endTime": "2026-05-10T06:00:00Z",
                            "value": {"stages": []},
                        }
                    ]
                }
            )
        return _mock_response({"dataPoints": []})

    with patch("requests.request", side_effect=side_effect):
        sync_google_health_morning_metrics(
            client,
            start_date=dt.date(2026, 5, 10),
            end_date=dt.date(2026, 5, 10),
            db_path=db_path,
        )

    from domestique_ai.processing.morning_metrics import fetch_morning_entry

    entry = fetch_morning_entry("2026-05-10", db_path=db_path)
    assert entry["sleep_score"] == 95
    assert entry["sleep_score_computed"] == 0
