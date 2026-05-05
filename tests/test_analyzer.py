"""Tests unitaires pour le module d'analyse."""

from __future__ import annotations

from domestique_ai.processing.analyzer import calculate_ctl_atl_tsb, calculate_tss


def test_calculate_tss_nominal():
    assert calculate_tss(3600, 250.0, 250.0) == 100.0


def test_calculate_tss_easy_ride():
    assert calculate_tss(3600, 125.0, 250.0) == 25.0


def test_calculate_tss_zero_ftp_returns_zero():
    assert calculate_tss(3600, 250.0, 0.0) == 0.0


def test_calculate_tss_zero_power_returns_zero():
    assert calculate_tss(3600, 0.0, 250.0) == 0.0


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
