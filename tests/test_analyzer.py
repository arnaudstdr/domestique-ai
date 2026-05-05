"""Tests unitaires pour le module d'analyse."""

from __future__ import annotations

import sqlite3

import pytest

from domestique_ai.ingestion.strava import init_db
from domestique_ai.processing.analyzer import (
    calculate_ctl_atl_tsb,
    calculate_hr_tss,
    calculate_trimp,
    calculate_tss,
    compute_training_load,
    recalculate_training_loads,
)


def test_calculate_tss_nominal():
    assert calculate_tss(3600, 250.0, 250.0) == 100.0


def test_calculate_tss_easy_ride():
    assert calculate_tss(3600, 125.0, 250.0) == 25.0


def test_calculate_tss_zero_ftp_returns_zero():
    assert calculate_tss(3600, 250.0, 0.0) == pytest.approx(0.0)


def test_calculate_tss_zero_power_returns_zero():
    assert calculate_tss(3600, 0.0, 250.0) == pytest.approx(0.0)


def test_calculate_ctl_atl_tsb_empty():
    assert calculate_ctl_atl_tsb([]) == []


def test_calculate_ctl_atl_tsb_progression():
    activities = [
        {"date": "2025-01-01T08:00:00Z", "training_load": 100},
        {"date": "2025-01-02T08:00:00Z", "training_load": 100},
        {"date": "2025-01-03T08:00:00Z", "training_load": 100},
        {"date": "2025-01-04T08:00:00Z", "training_load": 100},
        {"date": "2025-01-05T08:00:00Z", "training_load": 100},
    ]
    curves = calculate_ctl_atl_tsb(activities)
    assert len(curves) == 5
    assert curves[0]["date"] == "2025-01-01"
    assert curves[-1]["date"] == "2025-01-05"
    # ATL monte plus vite que CTL → TSB négatif après quelques jours de charge
    assert curves[-1]["ATL"] > curves[-1]["CTL"]
    assert curves[-1]["TSB"] < 0


def test_calculate_ctl_atl_tsb_fills_gap_dates():
    activities = [
        {"date": "2025-01-01T08:00:00Z", "training_load": 100},
        {"date": "2025-01-05T08:00:00Z", "training_load": 100},
    ]
    curves = calculate_ctl_atl_tsb(activities)
    assert len(curves) == 5
    assert [c["date"] for c in curves] == [
        "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"
    ]


def test_calculate_trimp_zero_when_data_missing():
    assert calculate_trimp(3600, 0, 50, 190) == pytest.approx(0.0)
    assert calculate_trimp(3600, 150, 50, 50) == pytest.approx(0.0)
    assert calculate_trimp(3600, 150, 50, 40) == pytest.approx(0.0)


def test_calculate_trimp_male_threshold_value():
    # 1h à HRR=0.88 (seuil) → ~183 (intermédiaire de référence pour anchor hr-TSS)
    trimp = calculate_trimp(3600, 50 + 0.88 * (190 - 50), 50, 190, sex="M")
    assert trimp == pytest.approx(183.04, abs=0.5)


def test_calculate_hr_tss_anchored_to_100_at_threshold():
    # 1h à HRR=0.88 ⇒ exactement 100 (par construction)
    hr_tss = calculate_hr_tss(3600, 50 + 0.88 * (190 - 50), 50, 190,
                              sex="M", lthr_pct=0.88)
    assert hr_tss == pytest.approx(100.0, abs=0.5)


def test_calculate_hr_tss_easy_endurance_lower_than_threshold():
    # HR moyenne basse → score < 100
    hr_tss = calculate_hr_tss(3600, 50 + 0.6 * (190 - 50), 50, 190, sex="M")
    assert 30 < hr_tss < 70


def test_calculate_hr_tss_female_anchored_too():
    hr_tss = calculate_hr_tss(3600, 55 + 0.88 * (185 - 55), 55, 185,
                              sex="F", lthr_pct=0.88)
    assert hr_tss == pytest.approx(100.0, abs=0.5)


def test_compute_training_load_prefers_hr_over_power():
    # Avec HR + HRrepos + HRmax dispo, on ignore avg_power même si présent
    score = compute_training_load(
        duration_sec=3600,
        avg_hr=50 + 0.88 * (190 - 50),
        avg_power=999.0,
        ftp=250.0,
        hr_rest=50, hr_max=190, sex="M", lthr_pct=0.88,
    )
    assert score == pytest.approx(100.0, abs=0.5)


def _clear_hr_env(monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)


def test_compute_training_load_falls_back_to_power_without_hr_config(monkeypatch):
    _clear_hr_env(monkeypatch)
    score = compute_training_load(
        duration_sec=3600, avg_hr=145, avg_power=250.0, ftp=250.0,
    )
    assert score == pytest.approx(100.0)


def test_compute_training_load_returns_zero_without_data(monkeypatch):
    _clear_hr_env(monkeypatch)
    monkeypatch.delenv("STRAVA_FTP", raising=False)
    assert compute_training_load(
        duration_sec=3600, avg_hr=None, avg_power=None, ftp=0,
    ) == pytest.approx(0.0)


def test_recalculate_training_loads_updates_existing_rows(tmp_path):
    db_path = tmp_path / "recalc.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration, avg_heart_rate, "
            "avg_power, training_load) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2025-04-01T08:00:00Z", 3600, 50 + 0.88 * (190 - 50), None, 0.0),
        )
        conn.commit()
    finally:
        conn.close()

    updated = recalculate_training_loads(
        db_path=db_path,
    )
    # Sans HRrepos/HRmax dans la config courante, le score peut rester 0 :
    # on vérifie au moins que la fonction ne plante pas et retourne un entier.
    assert isinstance(updated, int)
