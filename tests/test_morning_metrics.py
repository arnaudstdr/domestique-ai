"""Tests unitaires pour les métriques matinales."""

from __future__ import annotations

from pathlib import Path

import pytest

from domestique_ai.ingestion.strava import init_db
from domestique_ai.processing.morning_metrics import (
    compute_baselines,
    detect_morning_alerts,
    fetch_morning_entry,
    fetch_morning_history,
    save_morning_entry,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


def test_save_and_fetch_full_entry(db_path: Path):
    save_morning_entry(
        "2026-05-01",
        hrv_ms=58.0, resting_hr=48.0, sleep_hours=7.5,
        sleep_score=82, stress_score=25, notes="Bonne nuit",
        db_path=db_path,
    )
    entry = fetch_morning_entry("2026-05-01", db_path=db_path)
    assert entry == {
        "date": "2026-05-01",
        "hrv_ms": 58.0,
        "resting_hr": 48.0,
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "stress_score": 25,
        "notes": "Bonne nuit",
    }


def test_save_partial_entry(db_path: Path):
    save_morning_entry("2026-05-01", hrv_ms=55.0, db_path=db_path)
    entry = fetch_morning_entry("2026-05-01", db_path=db_path)
    assert entry["hrv_ms"] == 55.0
    assert entry["resting_hr"] is None
    assert entry["sleep_score"] is None


def test_save_empty_entry_returns_false(db_path: Path):
    assert save_morning_entry("2026-05-01", db_path=db_path) is False
    assert fetch_morning_entry("2026-05-01", db_path=db_path) is None


def test_save_overwrites_same_date(db_path: Path):
    save_morning_entry("2026-05-01", hrv_ms=55.0, db_path=db_path)
    save_morning_entry("2026-05-01", hrv_ms=60.0, db_path=db_path)
    entry = fetch_morning_entry("2026-05-01", db_path=db_path)
    assert entry["hrv_ms"] == 60.0


def test_fetch_history_window(db_path: Path):
    for i, hrv in enumerate([50, 55, 60, 65]):
        save_morning_entry(f"2026-05-0{i+1}", hrv_ms=hrv, db_path=db_path)
    full = fetch_morning_history(db_path=db_path)
    assert [e["date"] for e in full] == [
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04",
    ]
    last_2 = fetch_morning_history(days=2, db_path=db_path)
    assert [e["date"] for e in last_2] == ["2026-05-03", "2026-05-04"]


def test_baseline_with_history(db_path: Path):
    # Baseline = moyenne des entrées précédentes (hors dernière)
    save_morning_entry("2026-05-01", hrv_ms=60.0, db_path=db_path)
    save_morning_entry("2026-05-02", hrv_ms=58.0, db_path=db_path)
    save_morning_entry("2026-05-03", hrv_ms=62.0, db_path=db_path)
    save_morning_entry("2026-05-04", hrv_ms=50.0, db_path=db_path)

    result = compute_baselines("hrv_ms", window=14, db_path=db_path)
    assert result["available"] is True
    assert result["latest"] == 50.0
    assert result["baseline"] == pytest.approx(60.0)  # (60+58+62)/3
    assert result["delta_pct"] == pytest.approx(-16.6667, abs=0.01)
    assert result["sample_size"] == 3


def test_baseline_unavailable_with_single_entry(db_path: Path):
    save_morning_entry("2026-05-01", hrv_ms=60.0, db_path=db_path)
    result = compute_baselines("hrv_ms", db_path=db_path)
    assert result["available"] is False


def test_baseline_unknown_metric(db_path: Path):
    result = compute_baselines("foo", db_path=db_path)
    assert result["available"] is False


def test_alerts_hrv_drop(db_path: Path):
    # HRV baseline ~60, dernière à 50 → -16% → alerte (seuil 10%)
    for i, hrv in enumerate([60, 58, 62, 60, 50]):
        save_morning_entry(f"2026-05-0{i+1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert len(alerts) == 1
    assert alerts[0]["metric"] == "hrv_ms"
    assert alerts[0]["delta_pct"] < -10


def test_alerts_resting_hr_rise(db_path: Path):
    # FC repos baseline ~48, dernière à 56 → +16% → alerte
    for i, hr in enumerate([48, 47, 49, 48, 56]):
        save_morning_entry(f"2026-05-0{i+1}", resting_hr=hr, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert any(a["metric"] == "resting_hr" for a in alerts)


def test_alerts_no_alert_within_threshold(db_path: Path):
    # HRV stable à ±5% → pas d'alerte
    for i, hrv in enumerate([60, 58, 62, 60, 59]):
        save_morning_entry(f"2026-05-0{i+1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert alerts == []


def test_alerts_severity_critical(db_path: Path):
    # HRV chute > 20% (2× seuil) → critical
    for i, hrv in enumerate([60, 60, 60, 60, 40]):
        save_morning_entry(f"2026-05-0{i+1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert alerts[0]["severity"] == "critical"
