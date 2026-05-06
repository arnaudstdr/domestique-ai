"""Tests pour le module ``domestique_ai.llm.availability``."""

from __future__ import annotations

import pytest

from domestique_ai.llm.availability import (
    Availability,
    AvailabilityError,
    DayAvailability,
    load_availability,
    save_availability,
)

VALID_YAML = """
days:
  wednesday:
    max_duration_min: 90
    context: indoor
  thursday:
    max_duration_min: 90
    context: indoor
  saturday:
    max_duration_min: 240
    context: outdoor
  sunday:
    max_duration_min: 240
    context: outdoor

preferences:
  long_endurance_day: sunday
  intervals_day: thursday
"""


def _patch_path(tmp_path, monkeypatch, content: str | None = None):
    target = tmp_path / "availability.yaml"
    if content is not None:
        target.write_text(content)
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(target))
    return target


def test_load_availability_returns_none_when_missing(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content=None)
    assert load_availability() is None


def test_load_availability_parses_valid_yaml(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content=VALID_YAML)
    av = load_availability()
    assert av is not None
    assert [d.weekday for d in av.days] == [2, 3, 5, 6]
    wed = av.get(2)
    assert wed and wed.max_duration_min == 90 and wed.context == "indoor"
    sun = av.get(6)
    assert sun and sun.max_duration_min == 240 and sun.context == "outdoor"
    assert av.long_endurance_day == 6
    assert av.intervals_day == 3


def test_invalid_weekday_raises(tmp_path, monkeypatch):
    bad = "days:\n  mondai:\n    max_duration_min: 60\n    context: indoor\n"
    _patch_path(tmp_path, monkeypatch, content=bad)
    with pytest.raises(AvailabilityError, match="Nom de jour invalide"):
        load_availability()


def test_invalid_context_raises(tmp_path, monkeypatch):
    bad = "days:\n  monday:\n    max_duration_min: 60\n    context: amphibie\n"
    _patch_path(tmp_path, monkeypatch, content=bad)
    with pytest.raises(AvailabilityError, match="context invalide"):
        load_availability()


def test_too_short_duration_raises(tmp_path, monkeypatch):
    bad = "days:\n  monday:\n    max_duration_min: 10\n    context: indoor\n"
    _patch_path(tmp_path, monkeypatch, content=bad)
    with pytest.raises(AvailabilityError, match="trop court"):
        load_availability()


def test_missing_days_section_raises(tmp_path, monkeypatch):
    bad = "preferences:\n  long_endurance_day: sunday\n"
    _patch_path(tmp_path, monkeypatch, content=bad)
    with pytest.raises(AvailabilityError, match="section 'days'"):
        load_availability()


def test_preferences_referencing_unlisted_day_silently_ignored(tmp_path, monkeypatch):
    yaml_with_orphan_pref = """
days:
  saturday:
    max_duration_min: 180
    context: outdoor
preferences:
  long_endurance_day: monday
"""
    _patch_path(tmp_path, monkeypatch, content=yaml_with_orphan_pref)
    av = load_availability()
    assert av is not None
    # monday absent de days → préférence ignorée → fallback heuristique.
    assert av.long_endurance_day is None


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    target = _patch_path(tmp_path, monkeypatch)
    original = Availability(
        days=[
            DayAvailability(weekday=2, max_duration_min=90, context="indoor"),
            DayAvailability(weekday=6, max_duration_min=240, context="outdoor"),
        ],
        long_endurance_day=6,
    )
    save_availability(original)
    assert target.exists()
    reloaded = load_availability()
    assert reloaded is not None
    assert [d.weekday for d in reloaded.days] == [2, 6]
    assert reloaded.long_endurance_day == 6
    assert reloaded.intervals_day is None
