"""Tests du module ``domestique_ai.processing.today``."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from domestique_ai.processing import today as today_mod
from domestique_ai.processing.today import propose_workout_today


def _patch_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole DB, availability et profil sous tmp_path."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "today.db"))
    monkeypatch.setenv(
        "DOMESTIQUE_AI_AVAILABILITY_PATH",
        str(tmp_path / "avail.yaml"),
    )
    monkeypatch.setenv(
        "DOMESTIQUE_AI_PROFILE_PATH",
        str(tmp_path / "profile.yaml"),
    )
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    from domestique_ai.config import invalidate_profile_cache

    invalidate_profile_cache()


def _set_availability(tmp_path: Path, content: str) -> None:
    (tmp_path / "avail.yaml").write_text(content)


def _patch_tsb(monkeypatch: pytest.MonkeyPatch, tsb: float) -> None:
    """Force la valeur de TSB du jour en interceptant ``calculate_ctl_atl_tsb``.

    Plus stable qu'injecter des activités précises pour piloter la valeur.
    """

    def fake_curves(*_args, **_kwargs):  # noqa: ANN001, ANN002 — signature libre
        return [{"date": "2026-01-01", "CTL": 50.0, "ATL": 50.0, "TSB": tsb}]

    monkeypatch.setattr(today_mod, "calculate_ctl_atl_tsb", fake_curves)
    # Activités peu importe la valeur retournée puisqu'on a stubbé les courbes,
    # mais on évite la lecture disque inutile.
    monkeypatch.setattr(today_mod, "fetch_activities_from_db", lambda *_a, **_k: [])


def test_rest_day_when_weekday_not_in_availability(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    # Lundi explicitement OFF (seul samedi listé).
    _set_availability(
        tmp_path,
        "days:\n  saturday:\n    max_duration_min: 180\n    context: outdoor\n",
    )
    # Aujourd'hui = un lundi.
    monday = dt.date(2026, 1, 5)  # 2026-01-05 = lundi
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is True
    assert "lundi" in result["reason"].lower()


def test_endurance_when_tsb_optimal(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)  # zone "Optimal"
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is False
    assert result["workout"]["kind"] == "endurance"
    assert result["workout"]["target_zone"] == "z2"
    # Durée plafonnée par la dispo (90 min) → base endurance = 90, donc 90.
    assert result["workout"]["duration_min"] == 90
    assert result["tsb_zone"] == "Optimal"


def test_recovery_when_tsb_low(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, -25.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "recovery"
    assert result["workout"]["target_zone"] == "z1"
    assert result["tsb_zone"] == "Surentraîné"


def test_tempo_when_tsb_high_without_intervals_day(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 75\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 12.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "tempo"
    assert result["workout"]["target_zone"] == "z3"


def test_intervals_when_tsb_high_and_intervals_day_matches(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        """
days:
  monday:
    max_duration_min: 90
    context: indoor
preferences:
  intervals_day: monday
""",
    )
    _patch_tsb(monkeypatch, 12.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "intervals"
    assert result["workout"]["target_zone"] == "z4"


def test_duration_capped_by_availability(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 45\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    # Base endurance = 90, mais dispo plafonne à 45.
    assert result["workout"]["duration_min"] == 45


def test_available_min_override_forces_duration_and_overrides_off_day(
    tmp_path, monkeypatch
):
    """available_min force la durée ET débraye le check off-day."""
    _patch_paths(tmp_path, monkeypatch)
    # Aucun jour listé : sans override on serait en rest_day.
    _set_availability(
        tmp_path,
        "days:\n  saturday:\n    max_duration_min: 180\n    context: outdoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday, available_min=70)
    assert result["rest_day"] is False
    assert result["workout"]["duration_min"] == 70


def test_no_availability_file_yields_workout(tmp_path, monkeypatch):
    """Sans availability.yaml, on tombe sur le défaut (pas de contrainte)."""
    _patch_paths(tmp_path, monkeypatch)
    # Aucun fichier availability créé.
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is False
    assert result["workout"]["kind"] == "endurance"
    # Base endurance = 90.
    assert result["workout"]["duration_min"] == 90


def test_structure_is_populated(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    structure = result["workout"]["structure"]
    assert len(structure) >= 1
    assert all("phase" in s and "zone" in s for s in structure)


def test_estimated_tss_is_positive(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["estimated_tss"] > 0
