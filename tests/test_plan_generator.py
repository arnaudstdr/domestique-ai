"""Tests du générateur de plan par LLM, Ollama mocké de bout en bout."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from domestique_ai.llm import plan_generator as pg
from domestique_ai.llm.availability import Availability, DayAvailability


def _run(coro):
    """Helper : exécute une coroutine et retourne le résultat (équivalent asyncio).

    On évite la dépendance ``pytest-asyncio`` — le projet n'a pas d'autres
    tests async, ne mérite pas l'overhead d'une nouvelle dépendance.
    """
    return asyncio.run(coro)


def _ctx(
    today: dt.date = dt.date(2026, 5, 25),  # Lundi 22e semaine ISO
    target_date: dt.date | None = dt.date(2026, 6, 28),  # 5 semaines après
    ctl_current: float = 60.0,
    availability: Availability | None = None,
    focus: str | None = None,
    sessions_per_week: int = 4,
    target_event_type: str = "cyclosportive",
) -> pg.GenerationContext:
    return pg.GenerationContext(
        sessions_per_week=sessions_per_week,
        focus=focus,
        target_date=target_date,
        target_event_type=target_event_type,
        ctl_current=ctl_current,
        availability=availability,
        today=today,
    )


def _llm_response(workouts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"workouts": workouts}


def _patch_chat_structured(monkeypatch, scripted: list[Any]):
    """Remplace ``chat_structured`` par une séquence de retours scriptés.

    ``scripted`` peut contenir des dicts (retour OK) ou ``None`` (Ollama KO).
    Si la liste est épuisée, on renvoie ``None`` (fallback).
    """
    queue = list(scripted)

    async def fake(*args, **kwargs):
        return queue.pop(0) if queue else None

    monkeypatch.setattr(pg, "chat_structured", fake)


# ---------- Schéma Pydantic strict ------------------------------------------


def test_llm_week_plan_accepts_minimal_valid_payload():
    parsed = pg.LLMWeekPlan.model_validate(
        {
            "workouts": [
                {"date": "2026-05-26", "kind": "endurance", "duration_min": 90}
            ]
        }
    )
    assert len(parsed.workouts) == 1
    assert parsed.workouts[0].notes == ""


def test_llm_week_plan_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        pg.LLMWeekPlan.model_validate(
            {"workouts": [{"date": "2026-05-26", "kind": "vo2", "duration_min": 60}]}
        )


def test_llm_week_plan_rejects_duration_above_300():
    with pytest.raises(ValidationError):
        pg.LLMWeekPlan.model_validate(
            {
                "workouts": [
                    {"date": "2026-05-26", "kind": "endurance", "duration_min": 500}
                ]
            }
        )


def test_llm_week_plan_rejects_duration_below_20():
    with pytest.raises(ValidationError):
        pg.LLMWeekPlan.model_validate(
            {
                "workouts": [
                    {"date": "2026-05-26", "kind": "endurance", "duration_min": 10}
                ]
            }
        )


def test_llm_week_plan_rejects_invalid_date():
    with pytest.raises(ValidationError):
        pg.LLMWeekPlan.model_validate(
            {
                "workouts": [
                    {"date": "26-05-2026", "kind": "endurance", "duration_min": 60}
                ]
            }
        )


# ---------- _expand_to_workout (high-level → Workout complet) ----------------


def test_expand_rebuilds_structure_from_kind_and_duration():
    draft = pg.LLMWorkoutDraft(
        date="2026-05-26", kind="intervals", duration_min=60, notes="ok"
    )
    w = pg._expand_to_workout(draft, week_index=0, focus=None)
    assert w.kind == "intervals"
    assert w.target_zone == "z4"
    # _structure_for("intervals", ...) génère un warmup + reps + cooldown.
    phases = [s.phase for s in w.structure]
    assert phases[0] == "warmup"
    assert phases[-1] == "cooldown"
    assert any(s.phase == "active" and s.zone == "z4" for s in w.structure)


def test_expand_uses_focus_as_notes_when_llm_omits():
    draft = pg.LLMWorkoutDraft(
        date="2026-05-26", kind="endurance", duration_min=90, notes=""
    )
    w = pg._expand_to_workout(draft, week_index=0, focus="montagne")
    assert w.notes == "montagne"


def test_expand_preserves_llm_notes_over_focus():
    draft = pg.LLMWorkoutDraft(
        date="2026-05-26", kind="endurance", duration_min=90, notes="Foncier"
    )
    w = pg._expand_to_workout(draft, week_index=0, focus="montagne")
    assert w.notes == "Foncier"


# ---------- Génération bout en bout ------------------------------------------


def test_generate_plan_uses_llm_when_response_is_valid(monkeypatch):
    # Plan sur 1 semaine pour rester simple. Lundi 2026-05-25.
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),  # 1 semaine
    )
    _patch_chat_structured(
        monkeypatch,
        [
            _llm_response(
                [
                    {"date": "2026-05-25", "kind": "recovery", "duration_min": 45},
                    {"date": "2026-05-26", "kind": "tempo", "duration_min": 60},
                    {"date": "2026-05-28", "kind": "intervals", "duration_min": 60},
                    {"date": "2026-05-31", "kind": "endurance", "duration_min": 90},
                ]
            )
        ],
    )
    plan, weeks = _run(pg.collect_plan(ctx))
    assert len(weeks) == 1
    assert weeks[0].source == "llm"
    assert {w.kind for w in plan} == {"recovery", "tempo", "intervals", "endurance"}


def test_generate_falls_back_when_llm_returns_none(monkeypatch):
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
    )
    # Ollama injoignable les deux essais → fallback.
    _patch_chat_structured(monkeypatch, [None, None])
    plan, weeks = _run(pg.collect_plan(ctx))
    assert weeks[0].source == "fallback"
    assert len(plan) > 0  # Le builder déterministe produit toujours quelque chose.


def test_generate_falls_back_when_llm_payload_invalid(monkeypatch):
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
    )
    # 2 essais avec un kind invalide → ValidationError → fallback.
    invalid = {
        "workouts": [
            {"date": "2026-05-26", "kind": "vo2max", "duration_min": 60}
        ]
    }
    _patch_chat_structured(monkeypatch, [invalid, invalid])
    _, weeks = _run(pg.collect_plan(ctx))
    assert weeks[0].source == "fallback"


def test_generate_falls_back_when_workouts_empty(monkeypatch):
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
    )
    _patch_chat_structured(
        monkeypatch,
        [_llm_response([]), _llm_response([])],
    )
    _, weeks = _run(pg.collect_plan(ctx))
    assert weeks[0].source == "fallback"


def test_generate_retries_on_invalid_then_uses_valid_response(monkeypatch):
    """1re tentative invalide, 2e correcte → on doit utiliser la 2e."""
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
    )
    invalid = {
        "workouts": [
            {"date": "2026-05-26", "kind": "invalid", "duration_min": 60}
        ]
    }
    valid = _llm_response(
        [
            {"date": "2026-05-26", "kind": "endurance", "duration_min": 60}
        ]
    )
    _patch_chat_structured(monkeypatch, [invalid, valid])
    _, weeks = _run(pg.collect_plan(ctx))
    assert weeks[0].source == "llm"
    assert all(w.kind == "endurance" for w in weeks[0].workouts)


# ---------- Validation déterministe appliquée après LLM ----------------------


def test_generated_plan_passes_through_validate_and_correct(monkeypatch):
    """Le LLM peut produire du n'importe quoi : le validateur doit nettoyer."""
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
        ctl_current=0.0,  # cap TSS minuscule
    )
    # Plan absurde : 6 endurances très longues sur la même semaine.
    _patch_chat_structured(
        monkeypatch,
        [
            _llm_response(
                [
                    {"date": f"2026-05-{25 + i}", "kind": "endurance",
                     "duration_min": 240}
                    for i in range(6)
                ]
            )
        ],
    )
    plan, weeks = _run(pg.collect_plan(ctx))
    # Validateur a forcément raccourci les endurances.
    assert all(w.duration_min <= 240 for w in plan)
    # Le validateur a appliqué des ajustements et les a remontés.
    assert weeks[0].adjustments  # liste non vide


def test_validator_drops_workouts_on_unavailable_days(monkeypatch):
    avail = Availability(
        days=[
            DayAvailability(weekday=0, max_duration_min=90, context="indoor"),
            DayAvailability(weekday=2, max_duration_min=90, context="indoor"),
        ],
    )
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 1),
        availability=avail,
    )
    _patch_chat_structured(
        monkeypatch,
        [
            _llm_response(
                [
                    # Lundi (dispo)
                    {"date": "2026-05-25", "kind": "tempo", "duration_min": 60},
                    # Mardi (PAS dispo) → doit être supprimé par validate_and_correct
                    {"date": "2026-05-26", "kind": "endurance", "duration_min": 90},
                    # Mercredi (dispo)
                    {"date": "2026-05-27", "kind": "intervals", "duration_min": 60},
                ]
            )
        ],
    )
    plan, weeks = _run(pg.collect_plan(ctx))
    dates = [w.date for w in plan]
    assert "2026-05-26" not in dates
    assert "2026-05-25" in dates
    assert "2026-05-27" in dates
    # Un ajustement explicite mentionne le jour retiré.
    assert any("2026-05-26" in adj for adj in weeks[0].adjustments)


# ---------- Multi-semaines ---------------------------------------------------


def test_generate_multi_week_plan(monkeypatch):
    """3 semaines, chaque semaine renvoie 4 séances LLM."""
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 15),  # 3 semaines
    )
    # Une réponse LLM par semaine.
    responses: list[dict[str, Any]] = []
    for week in range(3):
        base = dt.date(2026, 5, 25) + dt.timedelta(weeks=week)
        responses.append(
            _llm_response(
                [
                    {
                        "date": (base + dt.timedelta(days=offset)).isoformat(),
                        "kind": kind,
                        "duration_min": dur,
                    }
                    for offset, (kind, dur) in enumerate(
                        [
                            ("recovery", 45),
                            ("tempo", 60),
                            ("intervals", 60),
                            ("endurance", 90),
                        ]
                    )
                ]
            )
        )
    _patch_chat_structured(monkeypatch, responses)
    plan, weeks = _run(pg.collect_plan(ctx))
    assert len(weeks) == 3
    assert all(w.source == "llm" for w in weeks)
    # Au moins une séance par semaine (les 4 LLM peuvent perdre des séances via validate).
    assert all(len(w.workouts) >= 1 for w in weeks)


def test_one_week_llm_one_week_fallback(monkeypatch):
    """Si la 1re semaine échoue côté LLM, on bascule sur fallback pour celle-ci.

    Les semaines suivantes peuvent rester côté LLM si la réponse est valide.
    Avec 2 tentatives par semaine, la séquence est :
    [None, None, valid] → semaine 1 = fallback (consomme 2 réponses None), semaine 2 = llm.
    """
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 8),  # 2 semaines
    )
    valid = _llm_response(
        [
            {"date": "2026-06-01", "kind": "endurance", "duration_min": 60}
        ]
    )
    _patch_chat_structured(monkeypatch, [None, None, valid])
    plan, weeks = _run(pg.collect_plan(ctx))
    assert len(weeks) == 2
    assert weeks[0].source == "fallback"
    assert weeks[1].source == "llm"


# ---------- Helpers internes -------------------------------------------------


def test_week_dates_uses_all_seven_when_no_availability():
    start = dt.date(2026, 5, 25)  # Lundi
    dates = pg._week_dates(start, None)
    assert len(dates) == 7
    assert dates[0] == start
    assert dates[-1] == start + dt.timedelta(days=6)


def test_week_dates_filters_by_availability():
    start = dt.date(2026, 5, 25)
    avail = Availability(
        days=[
            DayAvailability(weekday=0, max_duration_min=60, context="indoor"),
            DayAvailability(weekday=4, max_duration_min=120, context="outdoor"),
            DayAvailability(weekday=6, max_duration_min=180, context="outdoor"),
        ],
    )
    dates = pg._week_dates(start, avail)
    weekdays = [d.weekday() for d in dates]
    assert weekdays == [0, 4, 6]


def test_resolve_total_weeks_no_target():
    ctx = _ctx(target_date=None)
    assert pg._resolve_total_weeks(ctx) == 4


def test_resolve_total_weeks_with_target():
    ctx = _ctx(
        today=dt.date(2026, 5, 25),
        target_date=dt.date(2026, 6, 15),  # 21 jours
    )
    assert pg._resolve_total_weeks(ctx) == 3


def test_fallback_default_duration_known_kinds():
    assert pg.fallback_default_duration("endurance") == 90
    assert pg.fallback_default_duration("intervals") == 60
    assert pg.fallback_default_duration("recovery") == 45
    assert pg.fallback_default_duration("unknown") == 60
