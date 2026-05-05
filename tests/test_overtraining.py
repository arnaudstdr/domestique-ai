"""Tests unitaires pour la détection de signaux de surentraînement."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai.processing.overtraining import (
    MONOTONY_THRESHOLD,
    STRAIN_THRESHOLD,
    TSB_CHRONIC_THRESHOLD,
    WEEKLY_VOLUME_JUMP_PCT,
    compute_chronic_tsb,
    compute_monotony_strain,
    compute_weekly_jump,
)


def _activity(date: str, load: float) -> dict:
    return {"date": f"{date}T08:00:00Z", "training_load": load}


def _activities_n_days(loads: list[float]) -> list[dict]:
    """Crée N activités, une par jour, en partant du 1er mai 2026."""
    base = dt.date(2026, 5, 1)
    return [
        _activity((base + dt.timedelta(days=i)).isoformat(), load)
        for i, load in enumerate(loads)
    ]


def test_chronic_tsb_alert_below_threshold():
    # Charge élevée constante → ATL haut, CTL plus bas → TSB très négatif
    activities = _activities_n_days([150.0] * 30)
    result = compute_chronic_tsb(activities, days=7)
    assert result["available"] is True
    assert result["mean_tsb"] < TSB_CHRONIC_THRESHOLD
    assert result["alert"] is True


def test_chronic_tsb_no_alert_when_balanced():
    # Charge stable suffisamment longue → CTL et ATL convergent → TSB ~ 0
    activities = _activities_n_days([50.0] * 120)
    result = compute_chronic_tsb(activities, days=7)
    assert result["available"] is True
    assert result["alert"] is False


def test_chronic_tsb_unavailable_with_short_history():
    activities = _activities_n_days([50.0] * 3)
    result = compute_chronic_tsb(activities, days=7)
    assert result["available"] is False


def test_monotony_alert_when_constant_load():
    # Charge quasi constante → stdev faible → monotony élevée
    activities = _activities_n_days([100.0, 102.0, 98.0, 101.0,
                                     99.0, 100.0, 100.0])
    result = compute_monotony_strain(activities, days=7)
    assert result["available"] is True
    assert result["monotony"] > MONOTONY_THRESHOLD
    assert result["alert_monotony"] is True


def test_monotony_no_alert_when_varied_load():
    # Alternance fort/repos → stdev grand → monotony faible
    activities = _activities_n_days([200.0, 0.0, 150.0, 0.0,
                                     100.0, 0.0, 180.0])
    result = compute_monotony_strain(activities, days=7)
    assert result["available"] is True
    assert result["alert_monotony"] is False


def test_strain_alert_when_high_volume_and_monotony():
    # Charge élevée presque constante → strain énorme
    activities = _activities_n_days([150.0, 152.0, 148.0, 151.0,
                                     149.0, 150.0, 150.0])
    result = compute_monotony_strain(activities, days=7)
    assert result["available"] is True
    assert result["strain"] > STRAIN_THRESHOLD
    assert result["alert_strain"] is True


def test_monotony_unavailable_with_zero_load():
    activities = _activities_n_days([0.0] * 7)
    result = compute_monotony_strain(activities, days=7)
    assert result["available"] is False


def test_weekly_jump_alert_when_volume_doubles():
    # Semaine 1 : 50/jour, semaine 2 : 100/jour → +100%
    loads = [50.0] * 7 + [100.0] * 7
    activities = _activities_n_days(loads)
    result = compute_weekly_jump(activities)
    assert result["available"] is True
    assert result["delta_pct"] == pytest.approx(100.0)
    assert result["alert"] is True


def test_weekly_jump_no_alert_when_stable():
    loads = [80.0] * 14
    activities = _activities_n_days(loads)
    result = compute_weekly_jump(activities)
    assert result["alert"] is False
    assert abs(result["delta_pct"]) < WEEKLY_VOLUME_JUMP_PCT


def test_weekly_jump_resume_after_zero_week():
    # Semaine 1 : 0 (semaine off), semaine 2 : reprise
    loads = [0.0] * 7 + [60.0] * 7
    activities = _activities_n_days(loads)
    result = compute_weekly_jump(activities)
    assert result["available"] is True
    assert result["delta_pct"] is None
    assert result["alert"] is True
    assert "Reprise" in result["note"]


def test_weekly_jump_unavailable_with_short_history():
    activities = _activities_n_days([50.0] * 5)
    result = compute_weekly_jump(activities)
    assert result["available"] is False
