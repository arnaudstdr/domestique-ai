"""Tests pour le générateur de plan d'entraînement déterministe."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai.llm.availability import Availability, DayAvailability
from domestique_ai.processing.plan_builder import (
    HR_ZONE_KEYS,
    Workout,
    assert_zones_valid,
    build_training_plan,
    days_used,
)


def _user_availability(
    long_pref: int | None = None, intervals_pref: int | None = None
) -> Availability:
    """Cas utilisateur : mer/jeu HT 90 min + sam/dim outdoor 240 min."""
    return Availability(
        days=[
            DayAvailability(weekday=2, max_duration_min=90, context="indoor"),
            DayAvailability(weekday=3, max_duration_min=90, context="indoor"),
            DayAvailability(weekday=5, max_duration_min=240, context="outdoor"),
            DayAvailability(weekday=6, max_duration_min=240, context="outdoor"),
        ],
        long_endurance_day=long_pref,
        intervals_day=intervals_pref,
    )


def test_build_plan_with_target_date_covers_weeks():
    today = dt.date(2026, 5, 4)  # un lundi
    target = today + dt.timedelta(weeks=6)
    plan = build_training_plan(
        target_date=target,
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )
    # Tous les workouts ont une date >= today et <= target.
    assert all(today <= dt.date.fromisoformat(w.date) <= target for w in plan)
    # Au moins une séance par semaine de plan (6 semaines).
    weeks = {dt.date.fromisoformat(w.date).isocalendar()[1] for w in plan}
    assert len(weeks) >= 6


def test_build_plan_fallback_without_target():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=None,
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
        fallback_weeks=4,
    )
    assert len(plan) > 0
    last = max(dt.date.fromisoformat(w.date) for w in plan)
    assert (last - today).days <= 4 * 7 + 6


def test_taper_reduces_volume_in_last_two_weeks():
    today = dt.date(2026, 5, 4)
    target = today + dt.timedelta(weeks=8)
    plan = build_training_plan(
        target_date=target,
        ctl_current=50.0,
        sessions_per_week=4,
        start_date=today,
    )
    by_week: dict[str, float] = {}
    for w in plan:
        d = dt.date.fromisoformat(w.date)
        monday = d - dt.timedelta(days=d.weekday())
        by_week.setdefault(monday.isoformat(), 0)
        by_week[monday.isoformat()] += w.duration_min
    weeks_sorted = sorted(by_week.items())
    # La dernière semaine doit être plus légère qu'une semaine de charge typique
    last_week_volume = weeks_sorted[-1][1]
    middle_week_volume = weeks_sorted[len(weeks_sorted) // 2][1]
    assert last_week_volume < middle_week_volume


def test_intervals_swapped_in_recovery_week():
    today = dt.date(2026, 5, 4)
    target = today + dt.timedelta(weeks=6)
    plan = build_training_plan(
        target_date=target,
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )
    # Vérifie qu'aucune semaine entière n'est composée uniquement d'intervalles.
    by_week: dict[str, list[Workout]] = {}
    for w in plan:
        d = dt.date.fromisoformat(w.date)
        monday = d - dt.timedelta(days=d.weekday())
        by_week.setdefault(monday.isoformat(), []).append(w)
    for week_workouts in by_week.values():
        kinds = {w.kind for w in week_workouts}
        assert kinds != {"intervals"}


def test_progression_is_capped_by_ctl():
    today = dt.date(2026, 5, 4)
    target = today + dt.timedelta(weeks=12)
    # CTL très bas → progression capée.
    plan_low = build_training_plan(
        target_date=target,
        ctl_current=10.0,
        sessions_per_week=4,
        start_date=today,
    )
    plan_high = build_training_plan(
        target_date=target,
        ctl_current=80.0,
        sessions_per_week=4,
        start_date=today,
    )
    total_low = sum(w.estimated_tss for w in plan_low)
    total_high = sum(w.estimated_tss for w in plan_high)
    # Le plan d'un athlète plus entraîné doit produire au moins autant de TSS
    # cumulé qu'un plan d'un athlète peu entraîné (le cap CTL n'est pas un
    # plafond plus bas pour les CTL plus hauts).
    assert total_high >= total_low * 0.95


def test_intensity_share_is_polarized():
    """Z4-Z5 doit rester ≤ 25 % du temps total (loin des 80/20 stricts mais cohérent)."""
    today = dt.date(2026, 5, 4)
    target = today + dt.timedelta(weeks=8)
    plan = build_training_plan(
        target_date=target,
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )
    high_seconds = 0
    total_seconds = 0
    for w in plan:
        for s in w.structure:
            total_seconds += s.duration_sec
            if s.zone in ("z4", "z5"):
                high_seconds += s.duration_sec
    assert total_seconds > 0
    assert high_seconds / total_seconds <= 0.25


def test_invalid_sessions_per_week_raises():
    today = dt.date(2026, 5, 4)
    with pytest.raises(ValueError):
        build_training_plan(
            target_date=today + dt.timedelta(weeks=4),
            ctl_current=40.0,
            sessions_per_week=1,
            start_date=today,
        )


def test_workout_roundtrip_via_dict():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=4),
        ctl_current=40.0,
        sessions_per_week=3,
        start_date=today,
    )
    serialized = [w.to_dict() for w in plan]
    restored = [Workout.from_dict(d) for d in serialized]
    for original, copy in zip(plan, restored, strict=True):
        assert original.date == copy.date
        assert original.duration_min == copy.duration_min
        assert original.kind == copy.kind
        assert len(original.structure) == len(copy.structure)


def test_zones_are_all_valid():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=4),
        ctl_current=40.0,
        sessions_per_week=4,
        start_date=today,
    )
    assert_zones_valid(plan)
    valid = set(HR_ZONE_KEYS)
    assert all(w.target_zone in valid for w in plan)


# ----- Availability (contraintes utilisateur) ---------------------------------


def test_build_plan_uses_availability_days():
    today = dt.date(2026, 5, 4)  # lundi
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=3),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        start_date=today,
    )
    weekdays = {dt.date.fromisoformat(w.date).weekday() for w in plan}
    # Jamais hors mer/jeu/sam/dim.
    assert weekdays.issubset({2, 3, 5, 6})


def test_long_endurance_assigned_to_outdoor_max_day():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=3),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        start_date=today,
    )
    endurance_days = {
        dt.date.fromisoformat(w.date).weekday() for w in plan if w.kind == "endurance"
    }
    # Outdoor avec max le plus grand + tie-break weekday le plus tardif → dimanche (6).
    assert endurance_days == {6}


def test_intervals_assigned_to_indoor():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=3),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        start_date=today,
    )
    intervals_days = {
        dt.date.fromisoformat(w.date).weekday() for w in plan if w.kind == "intervals"
    }
    # Pas forcément présent toutes les semaines (récup), mais quand il est là
    # il doit tomber sur un jour indoor (mer ou jeu).
    if intervals_days:
        assert intervals_days.issubset({2, 3})


def test_duration_capped_by_max_duration_min():
    today = dt.date(2026, 5, 4)
    capped = Availability(
        days=[
            DayAvailability(weekday=2, max_duration_min=60, context="indoor"),
            DayAvailability(weekday=3, max_duration_min=60, context="indoor"),
            DayAvailability(weekday=5, max_duration_min=120, context="outdoor"),
            DayAvailability(weekday=6, max_duration_min=120, context="outdoor"),
        ],
    )
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=8),  # endurance progresserait à >120 sans cap
        ctl_current=80.0,  # CTL haut → progression rapide
        sessions_per_week=4,
        availability=capped,
        start_date=today,
    )
    # Aucune séance ne dépasse le cap du jour (60 ou 120 selon weekday).
    for w in plan:
        wd = dt.date.fromisoformat(w.date).weekday()
        cap = 60 if wd in {2, 3} else 120
        assert w.duration_min <= cap, f"{w.date} ({wd}): {w.duration_min} > {cap}"


def test_legacy_behavior_when_availability_none():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=3),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=None,
        start_date=today,
    )
    # Profil legacy : Lun/Mer/Ven/Dim = (0, 2, 4, 6).
    weekdays = {dt.date.fromisoformat(w.date).weekday() for w in plan}
    assert weekdays.issubset({0, 2, 4, 6})


def test_explicit_intervals_preference_overrides_heuristic():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=3),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(intervals_pref=3),
        start_date=today,
    )
    intervals_days = {
        dt.date.fromisoformat(w.date).weekday() for w in plan if w.kind == "intervals"
    }
    # Préférence explicite respectée → tous les intervals tombent un jeudi.
    if intervals_days:
        assert intervals_days == {3}


def test_workout_notes_include_context():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=2),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        start_date=today,
    )
    for w in plan:
        wd = dt.date.fromisoformat(w.date).weekday()
        if wd in {2, 3}:
            assert "Indoor" in w.notes, w
        elif wd in {5, 6}:
            assert "Outdoor" in w.notes, w


def test_focus_combined_with_context_in_notes():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=1),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        focus="objectif Marmotte",
        start_date=today,
    )
    sample = next(w for w in plan if dt.date.fromisoformat(w.date).weekday() == 6)
    assert "Outdoor" in sample.notes and "Marmotte" in sample.notes


def test_days_used_returns_unique_weekday_names():
    today = dt.date(2026, 5, 4)
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=2),
        ctl_current=40.0,
        sessions_per_week=4,
        availability=_user_availability(),
        start_date=today,
    )
    used = days_used(plan)
    assert set(used) == {"wednesday", "thursday", "saturday", "sunday"}
    # Tri par weekday : mer < jeu < sam < dim.
    assert used == ["wednesday", "thursday", "saturday", "sunday"]


def test_availability_with_fewer_days_than_sessions_caps_silently():
    today = dt.date(2026, 5, 4)
    only_two = Availability(
        days=[
            DayAvailability(weekday=5, max_duration_min=240, context="outdoor"),
            DayAvailability(weekday=6, max_duration_min=240, context="outdoor"),
        ],
    )
    plan = build_training_plan(
        target_date=today + dt.timedelta(weeks=2),
        ctl_current=40.0,
        sessions_per_week=4,  # > nb jours dispo → on plafonne à 2
        availability=only_two,
        start_date=today,
    )
    # 2 séances par semaine sur 2 semaines = 4 séances max (et probablement 4).
    assert 0 < len(plan) <= 4
