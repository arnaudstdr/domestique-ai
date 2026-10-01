"""Tests unitaires pour les métriques matinales."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.morning_metrics import (
    METRIC_COLUMNS,
    calculate_readiness_score,
    calculate_sleep_score,
    calculate_stress_score,
    compute_baselines,
    detect_morning_alerts,
    fetch_morning_entry,
    fetch_morning_history,
    format_morning_alert,
    latest_weight,
    latest_weight_entry,
    power_to_weight,
    readiness_band,
    save_morning_entry,
    set_weight,
    stress_band,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


def test_save_and_fetch_full_entry(db_path: Path):
    save_morning_entry(
        "2026-05-01",
        hrv_ms=58.0,
        resting_hr=48.0,
        sleep_hours=7.5,
        sleep_score=82,
        stress_score=25,
        notes="Bonne nuit",
        spo2_avg_pct=98.0,
        respiratory_rate_avg_bpm=14.0,
        skin_temp_delta_c=-0.2,
        sleep_deep_min=90,
        sleep_rem_min=120,
        sleep_light_min=240,
        sleep_awake_min=30,
        sleep_stages=[
            {
                "start": "2026-05-01T01:00:00+00:00",
                "end": "2026-05-01T02:00:00+00:00",
                "type": "DEEP",
            },
            {
                "start": "2026-05-01T02:00:00+00:00",
                "end": "2026-05-01T03:00:00+00:00",
                "type": "REM",
            },
        ],
        steps=8500,
        active_calories=420,
        readiness_score=72,
        sleep_score_computed=1,
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
        "spo2_avg_pct": 98.0,
        "respiratory_rate_avg_bpm": 14.0,
        "skin_temp_delta_c": -0.2,
        "sleep_deep_min": 90,
        "sleep_rem_min": 120,
        "sleep_light_min": 240,
        "sleep_awake_min": 30,
        "sleep_stages_json": json.dumps(
            [
                {
                    "start": "2026-05-01T01:00:00+00:00",
                    "end": "2026-05-01T02:00:00+00:00",
                    "type": "DEEP",
                },
                {
                    "start": "2026-05-01T02:00:00+00:00",
                    "end": "2026-05-01T03:00:00+00:00",
                    "type": "REM",
                },
            ],
            ensure_ascii=False,
        ),
        "steps": 8500,
        "active_calories": 420,
        "readiness_score": 72,
        "sleep_score_computed": 1,
        "weight_kg": None,
        "stress_score_computed": None,
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
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, db_path=db_path)
    full = fetch_morning_history(db_path=db_path)
    assert [e["date"] for e in full] == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
        "2026-05-04",
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
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert len(alerts) == 1
    assert alerts[0]["metric"] == "hrv_ms"
    assert alerts[0]["delta_pct"] < -10


def test_alerts_resting_hr_rise(db_path: Path):
    # FC repos baseline ~48, dernière à 56 → +16% → alerte
    for i, hr in enumerate([48, 47, 49, 48, 56]):
        save_morning_entry(f"2026-05-0{i + 1}", resting_hr=hr, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert any(a["metric"] == "resting_hr" for a in alerts)


def test_alerts_no_alert_within_threshold(db_path: Path):
    # HRV stable à ±5% → pas d'alerte
    for i, hrv in enumerate([60, 58, 62, 60, 59]):
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert alerts == []


def test_alerts_severity_critical(db_path: Path):
    # HRV chute > 20% (2× seuil) → critical
    for i, hrv in enumerate([60, 60, 60, 60, 40]):
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert alerts[0]["severity"] == "critical"


def test_calculate_sleep_score_perfect_night():
    # 7h30 de sommeil, deep 18%, REM 23%, awake 5% → score élevé
    score = calculate_sleep_score(
        sleep_hours=7.5,
        sleep_deep_min=81,
        sleep_rem_min=104,
        sleep_light_min=240,
        sleep_awake_min=25,
    )
    assert score is not None
    assert 80 <= score <= 100


def test_calculate_sleep_score_no_data():
    assert calculate_sleep_score(None, None, None, None, None) is None


def test_calculate_sleep_score_short_sleep():
    score = calculate_sleep_score(
        sleep_hours=5.0,
        sleep_deep_min=40,
        sleep_rem_min=60,
        sleep_light_min=140,
        sleep_awake_min=60,
    )
    assert score is not None
    assert score < 70


def test_calculate_readiness_score_with_baseline(db_path: Path):
    # Baseline HRV ~60, FC repos ~48
    for i, (hrv, hr) in enumerate([(60, 48), (58, 49), (62, 47), (60, 48)]):
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, resting_hr=hr, db_path=db_path)

    # Jour courant : HRV +10%, FC repos -2 bpm → readiness élevé
    save_morning_entry(
        "2026-05-05",
        hrv_ms=66.0,
        resting_hr=46.0,
        sleep_hours=8.0,
        db_path=db_path,
    )
    score = calculate_readiness_score(
        hrv_ms=66.0,
        resting_hr=46.0,
        sleep_hours=8.0,
        db_path=db_path,
    )
    assert score is not None
    assert score > 70


def test_calculate_readiness_score_no_data():
    assert calculate_readiness_score(None, None, None) is None


def test_readiness_band():
    assert readiness_band(90) == "PEAK"
    assert readiness_band(75) == "HIGH"
    assert readiness_band(60) == "BALANCED"
    assert readiness_band(40) == "LOW"
    assert readiness_band(20) == "VERY_LOW"
    assert readiness_band(None) is None


# ---------------------------------------------------------------------------
# Score de stress calculé
# ---------------------------------------------------------------------------


def _seed_autonomic(db_path: Path, days: int = 4, hrv: float = 60.0, hr: float = 48.0) -> None:
    for i in range(days):
        save_morning_entry(f"2026-05-0{i + 1}", hrv_ms=hrv, resting_hr=hr, db_path=db_path)


def test_calculate_stress_score_low(db_path: Path):
    for i, (hrv, hr) in enumerate([(60, 48)] * 4):
        save_morning_entry(
            f"2026-05-0{i + 1}",
            hrv_ms=hrv,
            resting_hr=hr,
            steps=8000,
            active_calories=400,
            db_path=db_path,
        )
    # HRV légèrement haute, FC repos basse, bonne nuit, activité stable → faible.
    score = calculate_stress_score(62.0, 47.0, 8.0, 85, 14.0, -0.1, 8000, 400, db_path=db_path)
    assert score is not None
    assert score < 40


def test_calculate_stress_score_high(db_path: Path):
    _seed_autonomic(db_path)
    # HRV en chute, FC repos élevée, nuit courte/mauvaise, temp cutanée haute → élevé.
    score = calculate_stress_score(45.0, 56.0, 5.0, 40, None, 0.6, None, None, db_path=db_path)
    assert score is not None
    assert score > 70


def test_calculate_stress_score_no_data():
    assert calculate_stress_score(None, None, None, None, None, None, None, None) is None


def test_calculate_stress_score_redistributes_weights(db_path: Path):
    _seed_autonomic(db_path)
    # Aucune donnée respi / temp / exertion : le score reste calculé sur
    # autonome + sommeil uniquement.
    score = calculate_stress_score(60.0, 48.0, 7.5, None, None, None, None, None, db_path=db_path)
    assert score is not None
    assert score < 40


def test_calculate_stress_score_sleep_only(db_path: Path):
    # Pas de baseline autonome (une seule entrée) mais sommeil court → calculé.
    save_morning_entry("2026-05-01", sleep_hours=5.0, db_path=db_path)
    score = calculate_stress_score(None, None, 5.0, None, None, None, None, None, db_path=db_path)
    assert score is not None
    assert score > 25


def test_stress_band():
    assert stress_band(10) == "LOW"
    assert stress_band(39) == "LOW"
    assert stress_band(40) == "MODERATE"
    assert stress_band(70) == "MODERATE"
    assert stress_band(71) == "HIGH"
    assert stress_band(None) is None


def test_alerts_skip_computed_stress(db_path: Path):
    for i, s in enumerate([30, 30, 30, 50]):
        save_morning_entry(
            f"2026-05-0{i + 1}", stress_score=s, stress_score_computed=1, db_path=db_path
        )
    alerts = detect_morning_alerts(db_path=db_path)
    assert all(a["metric"] != "stress_score" for a in alerts)


def test_alerts_manual_stress_triggers(db_path: Path):
    for i, s in enumerate([30, 30, 30, 50]):
        save_morning_entry(
            f"2026-05-0{i + 1}", stress_score=s, stress_score_computed=0, db_path=db_path
        )
    alerts = detect_morning_alerts(db_path=db_path)
    assert any(a["metric"] == "stress_score" for a in alerts)


# ---------------------------------------------------------------------------
# Poids (weight_kg)
# ---------------------------------------------------------------------------


def test_weight_kg_in_metric_columns():
    assert "weight_kg" in METRIC_COLUMNS


def test_save_weight_and_fetch(db_path: Path):
    save_morning_entry("2026-05-01", weight_kg=72.4, db_path=db_path)
    entry = fetch_morning_entry("2026-05-01", db_path=db_path)
    assert entry is not None
    assert entry["weight_kg"] == 72.4


def test_set_weight_preserves_other_fields(db_path: Path):
    save_morning_entry("2026-05-01", hrv_ms=55.0, sleep_hours=7.0, db_path=db_path)
    set_weight("2026-05-01", 70.5, db_path=db_path)
    entry = fetch_morning_entry("2026-05-01", db_path=db_path)
    assert entry["weight_kg"] == 70.5
    # Les autres métriques du jour ne doivent pas être écrasées.
    assert entry["hrv_ms"] == 55.0
    assert entry["sleep_hours"] == 7.0


def test_set_weight_creates_entry_without_other_metrics(db_path: Path):
    assert set_weight("2026-05-02", 69.0, db_path=db_path) is True
    entry = fetch_morning_entry("2026-05-02", db_path=db_path)
    assert entry["weight_kg"] == 69.0
    assert entry["hrv_ms"] is None


def test_latest_weight_returns_most_recent_non_null(db_path: Path):
    assert latest_weight(db_path=db_path) is None
    save_morning_entry("2026-05-01", weight_kg=72.0, db_path=db_path)
    save_morning_entry("2026-05-03", weight_kg=71.2, db_path=db_path)
    # Une date plus récente sans poids ne doit pas masquer le dernier poids connu.
    save_morning_entry("2026-05-05", hrv_ms=60.0, db_path=db_path)
    assert latest_weight(db_path=db_path) == 71.2
    assert latest_weight_entry(db_path=db_path) == ("2026-05-03", 71.2)


def test_power_to_weight():
    assert power_to_weight(250, 70) == 3.57
    assert power_to_weight(None, 70) is None
    assert power_to_weight(250, None) is None
    assert power_to_weight(250, 0) is None


def test_weight_does_not_trigger_alert(db_path: Path):
    # Variation importante mais aucune alerte attendue (direction 0).
    for i, w in enumerate([70.0, 70.0, 70.0, 80.0]):
        save_morning_entry(f"2026-05-0{i + 1}", weight_kg=w, db_path=db_path)
    alerts = detect_morning_alerts(db_path=db_path)
    assert all(a["metric"] != "weight_kg" for a in alerts)


def test_weight_baseline_computable(db_path: Path):
    for i, w in enumerate([72.0, 72.5, 71.5, 72.0]):
        save_morning_entry(f"2026-05-0{i + 1}", weight_kg=w, db_path=db_path)
    baseline = compute_baselines("weight_kg", db_path=db_path)
    assert baseline["available"] is True
    assert baseline["latest"] == 72.0


def test_format_morning_alert_uses_human_label():
    message = format_morning_alert(
        {
            "metric": "respiratory_rate_avg_bpm",
            "delta_pct": 10.2,
            "latest": 11.3,
            "latest_date": "2026-10-01",
        }
    )
    assert message == "Freq. resp. ↑ +10.2% vs baseline (11.3 le 2026-10-01)"


def test_format_morning_alert_arrow_down_on_drop():
    message = format_morning_alert(
        {
            "metric": "hrv_ms",
            "delta_pct": -16.7,
            "latest": 50.0,
            "latest_date": "2026-05-04",
        }
    )
    assert message.startswith("HRV ↓")


def test_format_morning_alert_falls_back_to_raw_metric():
    message = format_morning_alert(
        {
            "metric": "unknown_metric",
            "delta_pct": 12.0,
            "latest": 3.0,
            "latest_date": "2026-05-04",
        }
    )
    assert message.startswith("unknown_metric ↑")
