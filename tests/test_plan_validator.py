"""Tests des garde-fous déterministes appliqués à un plan d'entraînement."""

from __future__ import annotations

import pytest

from domestique_ai.llm.availability import (
    Availability,
    DayAvailability,
)
from domestique_ai.processing.plan_builder import Workout, WorkoutStep
from domestique_ai.processing.plan_validator import (
    validate_and_correct,
    weekly_high_intensity_share,
    weekly_tss,
)


def _mk_workout(
    date: str,
    kind: str = "endurance",
    duration_min: int = 60,
    high_intensity_sec: int = 0,
    estimated_tss: float | None = None,
    name: str | None = None,
) -> Workout:
    """Helper : Workout cohérent avec ``kind`` (structure + TSS estimé)."""
    target_zone = {
        "recovery": "z1",
        "endurance": "z2",
        "tempo": "z3",
        "intervals": "z4",
    }.get(kind, "z2")
    total_sec = duration_min * 60
    if high_intensity_sec > 0:
        active_sec = total_sec - 600
        structure = [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=300),
            WorkoutStep(phase="active", zone="z4", duration_sec=high_intensity_sec),
            WorkoutStep(
                phase="active",
                zone=target_zone,
                duration_sec=active_sec - high_intensity_sec,
            ),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=300),
        ]
    else:
        structure = [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=300),
            WorkoutStep(phase="active", zone=target_zone, duration_sec=total_sec - 600),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=300),
        ]
    tss = (
        estimated_tss
        if estimated_tss is not None
        else {"recovery": 30, "endurance": 55, "tempo": 75, "intervals": 95}[kind]
        * duration_min
        / 60
    )
    return Workout(
        date=date,
        name=name or f"{kind} {duration_min}'",
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone=target_zone,
        structure=structure,
        estimated_tss=round(tss, 1),
        notes="",
    )


def _mk_availability(
    days: dict[int, tuple[int, str]],
    long_endurance_day: int | None = None,
    intervals_day: int | None = None,
) -> Availability:
    """Helper : ``{0: (90, "outdoor"), 2: (60, "indoor"), …}``."""
    return Availability(
        days=[
            DayAvailability(weekday=w, max_duration_min=dur, context=ctx)
            for w, (dur, ctx) in days.items()
        ],
        long_endurance_day=long_endurance_day,
        intervals_day=intervals_day,
    )


# ---------- Garde-fou 1 : disponibilité --------------------------------------


def test_validator_removes_workout_on_unavailable_day():
    # Mardi 2026-05-26 : pas dans la dispo (seulement lundi).
    plan = [
        _mk_workout("2026-05-25"),  # Lundi
        _mk_workout("2026-05-26"),  # Mardi : non disponible
    ]
    avail = _mk_availability({0: (90, "outdoor")})
    out, adjustments = validate_and_correct(plan, availability=avail)
    assert [w.date for w in out] == ["2026-05-25"]
    assert any("hors disponibilité" in a for a in adjustments)


def test_validator_truncates_workout_to_day_max_duration():
    # Disponibilité lundi 45 min, plan propose 90 min → tronqué à 45.
    plan = [_mk_workout("2026-05-25", duration_min=90)]
    avail = _mk_availability({0: (45, "outdoor")})
    out, adjustments = validate_and_correct(plan, availability=avail)
    assert len(out) == 1
    assert out[0].duration_min == 45
    assert any("plafonnée" in a for a in adjustments)


def test_validator_keeps_workout_within_day_max():
    plan = [_mk_workout("2026-05-25", duration_min=60)]
    avail = _mk_availability({0: (90, "outdoor")})
    out, adjustments = validate_and_correct(plan, availability=avail)
    assert out[0].duration_min == 60
    # Aucune correction si tout est dans les bornes.
    assert all(adj.split(":")[0].strip() != "2026-05-25" for adj in adjustments)


def test_validator_skips_availability_when_none_provided():
    plan = [_mk_workout("2026-05-26"), _mk_workout("2026-05-27")]
    out, adjustments = validate_and_correct(plan, availability=None)
    assert len(out) == 2
    assert adjustments == []


# ---------- Garde-fou 2 : repos hebdomadaire ---------------------------------


def test_validator_enforces_max_six_sessions_per_week():
    # 7 séances sur une même semaine → suppression de la moins prioritaire (récup).
    plan = [
        _mk_workout("2026-05-25", kind="recovery"),
        _mk_workout("2026-05-26", kind="tempo"),
        _mk_workout("2026-05-27", kind="endurance", duration_min=90),
        _mk_workout("2026-05-28", kind="intervals", high_intensity_sec=600),
        _mk_workout("2026-05-29", kind="tempo"),
        _mk_workout("2026-05-30", kind="endurance", duration_min=90),
        _mk_workout("2026-05-31", kind="endurance", duration_min=120),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=80.0)
    assert len(out) == 6
    # La récup (priorité la plus basse) a été retirée.
    assert all(w.kind != "recovery" for w in out)
    assert any("repos hebdo" in a for a in adjustments)


def test_validator_keeps_six_sessions_per_week_untouched():
    plan = [_mk_workout(f"2026-05-{25 + i}") for i in range(6)]
    out, adjustments = validate_and_correct(plan, ctl_current=80.0)
    assert len(out) == 6
    assert all("repos hebdo" not in a for a in adjustments)


# ---------- Garde-fou 3 : polarisation 80/20 ---------------------------------


def test_validator_converts_intervals_when_high_intensity_exceeds_25_pct():
    # 3 séances intervals de 60 min × 30 min Z4 = 90 min Z4 sur 3 séances
    # totalisant ~165 min actives → 54 %, bien au-dessus de 25 %.
    plan = [
        _mk_workout("2026-05-25", kind="intervals", high_intensity_sec=1800),
        _mk_workout("2026-05-27", kind="intervals", high_intensity_sec=1800),
        _mk_workout("2026-05-29", kind="intervals", high_intensity_sec=1800),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=80.0)
    shares = weekly_high_intensity_share(out)
    # Toutes les semaines doivent passer sous 25 % après conversion.
    assert all(share <= 0.25 + 1e-6 for share in shares.values())
    assert any("polarisation" in a for a in adjustments)


def test_validator_keeps_polarized_plan_untouched():
    # 1 séance intervals 60' (24 min Z4) sur une semaine de 270 min actives
    # → 8.9 %, bien sous 25 %. Aucune conversion nécessaire.
    plan = [
        _mk_workout("2026-05-25", kind="endurance", duration_min=90),
        _mk_workout("2026-05-27", kind="intervals", high_intensity_sec=1440),
        _mk_workout("2026-05-29", kind="endurance", duration_min=90),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=80.0)
    # Aucun intervals converti.
    intervals_after = [w for w in out if w.kind == "intervals"]
    assert len(intervals_after) == 1
    assert all("polarisation" not in a for a in adjustments)


# ---------- Garde-fou 4 : plafond TSS hebdo ----------------------------------


def test_validator_truncates_endurance_when_tss_exceeds_cap():
    # Cap = (max(20, CTL) + 5 * 0) * 7 = 20 × 7 = 140 (semaine 0, CTL très bas).
    # Plan : 3 endurances très longues → bien au-dessus de 140 TSS.
    plan = [
        _mk_workout("2026-05-25", kind="endurance", duration_min=240),
        _mk_workout("2026-05-27", kind="endurance", duration_min=240),
        _mk_workout("2026-05-29", kind="endurance", duration_min=240),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=0.0)
    total_tss = sum(w.estimated_tss for w in out)
    assert total_tss <= 140 + 1e-3
    assert any("plafond TSS" in a for a in adjustments)


def test_validator_keeps_plan_with_tss_within_cap():
    # CTL 80 → cap ≈ 80 × 7 = 560 TSS / sem.
    # Plan : 4 séances modérées totalisant ~350 TSS.
    plan = [
        _mk_workout("2026-05-25", kind="recovery"),
        _mk_workout("2026-05-26", kind="tempo"),
        _mk_workout("2026-05-28", kind="intervals", high_intensity_sec=900),
        _mk_workout("2026-05-30", kind="endurance", duration_min=120),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=80.0)
    assert sum(w.estimated_tss for w in out) <= 80 * 7 + 1e-3
    assert all("plafond TSS" not in a for a in adjustments)


# ---------- Cas combinés -----------------------------------------------------


def test_validator_handles_empty_plan():
    out, adjustments = validate_and_correct([], ctl_current=50.0)
    assert out == []
    assert adjustments == []


def test_validator_handles_multi_week_plan_independently():
    """Les corrections doivent être appliquées par semaine, pas globalement."""
    plan = [
        # Semaine 1 : OK (~150 TSS).
        _mk_workout("2026-05-25", kind="tempo"),
        _mk_workout("2026-05-28", kind="endurance", duration_min=90),
        # Semaine 2 : dépassement de cap.
        _mk_workout("2026-06-01", kind="endurance", duration_min=240),
        _mk_workout("2026-06-03", kind="endurance", duration_min=240),
        _mk_workout("2026-06-05", kind="endurance", duration_min=240),
    ]
    out, adjustments = validate_and_correct(plan, ctl_current=20.0)
    # 5 séances de retour minimum (rien n'a été retiré).
    assert len(out) == 5
    tss_by_week = weekly_tss(out)
    assert all(total <= 25 * 7 + 1e-3 for total in tss_by_week.values())
    # Une correction au moins sur la semaine 2.
    assert any("2026-06" in a for a in adjustments)


def test_validator_applies_availability_before_other_rules():
    """Une séance retirée par dispo ne doit pas compter dans le TSS hebdo."""
    plan = [
        _mk_workout("2026-05-25", kind="endurance", duration_min=180),
        _mk_workout("2026-05-26", kind="endurance", duration_min=180),  # mardi
    ]
    # Seul le lundi est dispo → on perd le mardi.
    avail = _mk_availability({0: (180, "outdoor")})
    out, adjustments = validate_and_correct(plan, ctl_current=100.0, availability=avail)
    assert len(out) == 1
    assert out[0].date == "2026-05-25"
    assert any("hors disponibilité" in a for a in adjustments)


def test_validator_combines_multiple_rules():
    """Multi-correction : repos hebdo + plafond TSS sur la même semaine.

    Note : quand l'input est extrême (CTL très bas + 7 séances très longues),
    le validateur fait du best-effort — il raccourcit jusqu'au plancher
    physiologique (45 min pour l'endurance) sans descendre plus bas.
    """
    plan = [_mk_workout(f"2026-05-{25 + i}", duration_min=150) for i in range(7)]
    input_tss = sum(w.estimated_tss for w in plan)
    out, adjustments = validate_and_correct(plan, ctl_current=10.0)
    # Repos hebdo : au plus 6 séances.
    assert len(out) <= 6
    # Plafond TSS visé : réduction substantielle même si plancher atteint.
    output_tss = sum(w.estimated_tss for w in out)
    assert output_tss < input_tss / 2
    # Au moins 2 types de corrections (repos hebdo, plafond TSS, cadence type).
    types = {
        ("repos" in a or "plafond" in a or "cadence" in a) for a in adjustments
    }
    assert types == {True}


def test_validator_preserves_workout_dates_after_correction():
    """Une correction de durée ne doit pas décaler la date de la séance."""
    plan = [_mk_workout("2026-05-25", duration_min=240)]
    out, _ = validate_and_correct(plan, ctl_current=0.0)
    assert out[0].date == "2026-05-25"


def test_validator_adjustments_describe_modified_workouts():
    """Chaque ajustement doit mentionner la date de la séance impactée."""
    plan = [
        _mk_workout("2026-05-25", duration_min=240, kind="endurance"),
        _mk_workout("2026-05-26", duration_min=240, kind="endurance"),
    ]
    _, adjustments = validate_and_correct(plan, ctl_current=0.0)
    for adj in adjustments:
        # Chaque ligne d'ajustement commence par une date ISO YYYY-MM-DD.
        assert adj[:10].count("-") == 2


def test_validator_does_not_create_workouts():
    """Le validateur ne doit jamais ajouter de séance."""
    plan = [_mk_workout("2026-05-25")]
    out, _ = validate_and_correct(plan, ctl_current=80.0)
    assert len(out) <= len(plan)


def test_validator_respects_min_duration_of_20_min():
    """Une séance ne peut pas être raccourcie sous 20 min (plancher physiologique)."""
    plan = [_mk_workout("2026-05-25", duration_min=240, kind="endurance")]
    out, _ = validate_and_correct(plan, ctl_current=0.0)
    assert all(w.duration_min >= 20 for w in out)


# ---------- Garde-fou 5 : cadence d'intensité par type -----------------------


def test_validator_adds_intensity_on_all_endurance_charge_week():
    """Une semaine de charge 100 % Z2 reçoit une séance d'intervalles (course)."""
    plan = [
        _mk_workout("2026-05-25", kind="endurance", duration_min=90),
        _mk_workout("2026-05-27", kind="endurance", duration_min=90),
        _mk_workout("2026-05-29", kind="endurance", duration_min=120),
    ]
    out, adjustments = validate_and_correct(
        plan, ctl_current=60.0, target_event_type="course", total_weeks=4
    )
    kinds = {w.kind for w in out}
    assert "intervals" in kinds
    assert any("cadence" in a for a in adjustments)
    # Le plafond reste respecté.
    assert sum(w.estimated_tss for w in out) <= 60 * 7 + 1e-3


def test_validator_skips_cadence_when_intervals_present():
    """Une semaine contenant déjà des intervalles n'est pas modifiée par la cadence."""
    plan = [
        _mk_workout("2026-06-01", kind="intervals", duration_min=60, high_intensity_sec=1800),
        _mk_workout("2026-06-03", kind="endurance", duration_min=90),
        _mk_workout("2026-06-05", kind="endurance", duration_min=90),
    ]
    out, adj = validate_and_correct(
        plan, ctl_current=60.0, target_event_type="course", total_weeks=4
    )
    assert not any("cadence" in a for a in adj)
    assert any(w.kind == "intervals" for w in out)


def test_validator_keeps_plan_within_cap_when_adding_intensity():
    """La cadence d'intensité ne doit jamais faire dépasser le plafond TSS."""
    plan = [
        _mk_workout("2026-05-25", kind="endurance", duration_min=120),
        _mk_workout("2026-05-27", kind="endurance", duration_min=120),
        _mk_workout("2026-05-29", kind="endurance", duration_min=120),
    ]
    out, _ = validate_and_correct(
        plan, ctl_current=10.0, target_event_type="forme", total_weeks=4
    )
    assert sum(w.estimated_tss for w in out) <= 20 * 7 + 1e-3


# ---------- Garde-fou 6 : sortie longue sur le jour dédié --------------------


def _mk_avail_with_long_sunday():
    return _mk_availability(
        {0: (240, "outdoor"), 3: (60, "indoor"), 5: (240, "outdoor"), 6: (240, "outdoor")},
        long_endurance_day=6,
    )


def test_validator_moves_longest_endurance_to_long_day():
    """La plus longue endurance est déplacée sur le jour long (dimanche)."""
    # 2026-05-25 = lundi, 05-30 = samedi (endurance longue), 05-31 = dimanche (endurance courte).
    plan = [
        _mk_workout("2026-05-25", kind="tempo", duration_min=60),
        _mk_workout("2026-05-28", kind="recovery", duration_min=45),
        _mk_workout("2026-05-30", kind="endurance", duration_min=90),
        _mk_workout("2026-05-31", kind="endurance", duration_min=60),
    ]
    out, adjustments = validate_and_correct(
        plan, ctl_current=50.0, availability=_mk_avail_with_long_sunday(),
        target_event_type="forme", total_weeks=4,
    )
    sunday = next(w for w in out if w.date == "2026-05-31")
    saturday = next(w for w in out if w.date == "2026-05-30")
    assert sunday.kind == "endurance"
    assert sunday.duration_min >= saturday.duration_min
    assert any("sortie longue" in a for a in adjustments)


def test_validator_lengthens_short_long_ride_when_cap_allows():
    """Une sortie longue trop courte sur le jour dédié est portée à ≥ 90 min."""
    plan = [
        _mk_workout("2026-05-25", kind="tempo", duration_min=60),
        _mk_workout("2026-05-28", kind="recovery", duration_min=45),
        _mk_workout("2026-05-30", kind="endurance", duration_min=60),
        _mk_workout("2026-05-31", kind="endurance", duration_min=75),
    ]
    out, adjustments = validate_and_correct(
        plan, ctl_current=80.0, availability=_mk_avail_with_long_sunday(),
        target_event_type="forme", total_weeks=4,
    )
    sunday = next(w for w in out if w.date == "2026-05-31")
    assert sunday.kind == "endurance"
    assert sunday.duration_min >= 75
    assert any("sortie longue" in a for a in adjustments)


def test_validator_resets_estimated_tss_after_duration_change():
    """Quand on raccourcit, ``estimated_tss`` doit être recalculé."""
    plan = [_mk_workout("2026-05-25", duration_min=240, kind="endurance")]
    out, _ = validate_and_correct(plan, ctl_current=0.0)
    # endurance ~55 TSS/h. À 60 min on attend ~55, à 45 min ~41.
    for w in out:
        expected = 55 * w.duration_min / 60
        assert abs(w.estimated_tss - expected) <= 1.0


# ---------- Helpers exposés --------------------------------------------------


def test_weekly_tss_groups_by_iso_week():
    plan = [
        _mk_workout("2026-05-25", kind="tempo"),  # Semaine ISO 22
        _mk_workout("2026-05-31", kind="tempo"),  # Encore semaine 22 (dimanche)
        _mk_workout("2026-06-01", kind="tempo"),  # Semaine 23 (lundi)
    ]
    weekly = weekly_tss(plan)
    assert len(weekly) == 2
    # 2 séances dans la première semaine, 1 dans la seconde.
    counts = sorted(weekly.values())
    assert counts == [pytest.approx(75.0), pytest.approx(150.0)]


def test_weekly_high_intensity_share_is_zero_when_no_z4_z5():
    plan = [
        _mk_workout("2026-05-25", kind="endurance"),
        _mk_workout("2026-05-27", kind="tempo"),
    ]
    shares = weekly_high_intensity_share(plan)
    assert all(s == 0.0 for s in shares.values())
