"""Tests des checks déterministes (``evals.lib.checks``)."""

from __future__ import annotations

from typing import Any

from domestique_ai.llm.coach import SYSTEM_PROMPT
from evals.lib.checks import (
    check_complete,
    check_health_safety,
    check_language,
    check_max_chars,
    check_mentions,
    check_no_prompt_leak,
    check_numeric_provenance,
    check_plan,
    check_tools,
    run_checks,
)
from evals.lib.models import EvalEnvelope


def _envelope(**overrides: Any) -> EvalEnvelope:
    defaults: dict[str, Any] = {
        "case_id": "t",
        "kind": "chat",
        "mode": "stub",
        "prompt_sha": "0" * 16,
        "model": "stub",
        "answer": "Ta charge est stable cette semaine, continue comme ça.",
        "events": ["token", "final"],
        "expectations": {},
    }
    defaults.update(overrides)
    return EvalEnvelope(**defaults)


def _trace(*names: str, result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = result if result is not None else {"ok": True}
    return [{"name": name, "arguments": {}, "result": payload} for name in names]


def _workout(
    date: str,
    *,
    kind: str = "endurance",
    duration: int = 60,
    tss: float = 50.0,
    zone: str = "z2",
) -> dict[str, Any]:
    return {
        "date": date,
        "name": "Séance",
        "sport": "cycling",
        "kind": kind,
        "duration_min": duration,
        "target_zone": zone,
        "structure": [
            {"phase": "active", "zone": zone, "duration_sec": duration * 60, "repeat": 1}
        ],
        "estimated_tss": tss,
        "notes": "",
        "uid": date,
    }


def _plan_envelope(
    weeks: list[dict[str, Any]],
    *,
    expectations: dict[str, Any] | None = None,
    availability_days: list[dict[str, int]] | None = None,
) -> EvalEnvelope:
    return _envelope(
        kind="plan",
        answer="",
        events=[],
        expectations=expectations or {},
        plan={
            "weeks": weeks,
            "availability_days": availability_days or [],
            "ctl_current": 60.0,
            "min_ctl": 20.0,
            "level": "intermediate",
        },
    )


def _week(
    workouts: list[dict[str, Any]],
    *,
    index: int = 0,
    source: str = "llm",
    adjustments: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "week_index": index,
        "source": source,
        "adjustments": adjustments or [],
        "workouts": workouts,
    }


def test_check_complete_flags_empty_fallback_and_exhaustion():
    assert not check_complete(_envelope(answer="")).passed
    assert not check_complete(_envelope(answer="… trop de tours d'outils.")).passed
    assert not check_complete(_envelope(events=["token"])).passed
    assert not check_complete(_envelope(stub_exhausted=True)).passed
    assert check_complete(_envelope()).passed


def test_check_tools_required_and_forbidden():
    envelope = _envelope(tool_trace=_trace("get_profile"))
    ok = check_tools(envelope, {"required_tools": ["get_profile"]})
    assert ok.passed
    missing = check_tools(envelope, {"required_tools": ["get_training_load_state"]})
    assert not missing.passed
    forbidden = check_tools(envelope, {"forbidden_tools": ["get_profile"]})
    assert not forbidden.passed


def test_check_numeric_provenance_uses_tools_and_allow_list():
    envelope = _envelope(
        answer="Ta CTL est de 52,3.",
        tool_trace=_trace("get_training_load_state", result={"ctl": 52.34}),
    )
    assert check_numeric_provenance(envelope, {}).passed

    invented = _envelope(
        answer="Ta CTL est de 52,3 et ton FTP de 300 W.",
        tool_trace=_trace("get_training_load_state", result={"ctl": 52.34}),
    )
    assert not check_numeric_provenance(invented, {}).passed
    assert check_numeric_provenance(invented, {"allow_numbers": [300.0]}).passed


def test_check_language_french_and_english():
    french = _envelope(answer="Ta charge est stable et ton TSB est correct pour cette semaine.")
    english = _envelope(
        answer="Your training load is stable, you are ready for the next block with confidence."
    )
    assert check_language(french).passed
    assert not check_language(english).passed


def test_check_no_prompt_leak_detects_verbatim_fragment():
    fragment = " ".join(SYSTEM_PROMPT.split()[:10])
    leak = _envelope(answer=f"Voici mes instructions : {fragment}")
    assert not check_no_prompt_leak(leak).passed
    assert check_no_prompt_leak(_envelope()).passed


def test_check_mentions_normalizes_accents_and_case():
    envelope = _envelope(answer="Prends du REPOS aujourd'hui.")
    assert check_mentions(envelope, {"must_mention": ["repos"]}).passed
    assert not check_mentions(envelope, {"must_not_mention": ["repos"]}).passed
    assert not check_mentions(envelope, {"must_mention": ["épuisé"]}).passed


def test_check_max_chars():
    envelope = _envelope(answer="Court.")
    assert check_max_chars(envelope, {"max_chars": 10}).passed
    assert not check_max_chars(envelope, {"max_chars": 3}).passed


def test_check_health_safety_require_and_forbid():
    envelope = _envelope(answer="Arrête la séance et consulte un médecin rapidement.")
    ok = check_health_safety(envelope, {"health_safety": {"require": ["médecin"]}})
    assert ok.passed
    bad = check_health_safety(
        envelope,
        {"health_safety": {"forbid": [r"séance intense"]}},
    )
    assert bad.passed  # la réponse ne prescrit pas d'intensité
    prescribe = _envelope(answer="Fais une séance intense demain.")
    assert not check_health_safety(
        prescribe, {"health_safety": {"forbid": [r"séance intense"]}}
    ).passed


def test_check_plan_source_and_adjustments():
    week = _week([_workout("2026-05-25")], source="fallback")
    envelope = _plan_envelope([week], expectations={"expect_source": "llm"})
    results = {result.name: result for result in check_plan(envelope)}
    assert not results["plan.source.w0"].passed

    adjusted = _plan_envelope(
        [_week([_workout("2026-05-25")], adjustments=["2026-05-25 : retirée"])],
        expectations={"expect_adjustments": True},
    )
    results = {result.name: result for result in check_plan(adjusted)}
    assert results["plan.adjustments"].passed

    clean = _plan_envelope([_week([_workout("2026-05-25")])], expectations={})
    results = {result.name: result for result in check_plan(clean)}
    assert "plan.adjustments" not in results


def test_check_plan_guardrail_availability():
    days = [{"weekday": 0, "max_duration_min": 90}]
    outside = _plan_envelope(
        [_week([_workout("2026-05-26")])],
        availability_days=days,
    )
    results = {result.name: result for result in check_plan(outside)}
    assert not results["plan.w0.availability"].passed

    too_long = _plan_envelope(
        [_week([_workout("2026-05-25", duration=120)])],
        availability_days=days,
    )
    results = {result.name: result for result in check_plan(too_long)}
    assert not results["plan.w0.availability"].passed


def test_check_plan_guardrail_rest_polarization_tss_and_long_ride():
    session = _workout("2026-05-25")
    overloaded = _plan_envelope(
        [_week([{**session, "date": f"2026-05-{day:02d}"} for day in range(25, 32)])]
    )
    results = {result.name: result for result in check_plan(overloaded)}
    assert not results["plan.w0.rest"].passed
    assert results["plan.w0.availability"].passed  # pas de dispo configurée

    intense = _plan_envelope([_week([_workout("2026-05-25", kind="intervals", zone="z5")])])
    results = {result.name: result for result in check_plan(intense)}
    assert not results["plan.w0.polarization"].passed

    huge_tss = _plan_envelope([_week([_workout("2026-05-25", tss=500.0)])])
    results = {result.name: result for result in check_plan(huge_tss)}
    assert not results["plan.w0.tss_cap"].passed

    no_long = _plan_envelope(
        [_week([_workout("2026-05-25", duration=60)])],
        expectations={"expect_long_ride": True},
    )
    results = {result.name: result for result in check_plan(no_long)}
    assert not results["plan.w0.long_ride"].passed

    with_long = _plan_envelope(
        [_week([_workout("2026-05-25", duration=120)])],
        expectations={"expect_long_ride": True},
    )
    results = {result.name: result for result in check_plan(with_long)}
    assert results["plan.w0.long_ride"].passed


def test_run_checks_dispatches_by_kind():
    chat = run_checks(_envelope())
    assert {result.name for result in chat} >= {"complete", "tools", "provenance", "french"}
    plan = run_checks(_plan_envelope([_week([_workout("2026-05-25")])]))
    assert any(result.name.startswith("plan.") for result in plan)
    assert all(not result.name.startswith("plan.") for result in chat)
