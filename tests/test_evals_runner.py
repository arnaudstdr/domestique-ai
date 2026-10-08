"""Tests du runner d'évaluation (``evals.lib.runner``).

Ces tests exercent le vrai pipeline (tools, DB, boucle agentique, validateur de
plan) avec uniquement les appels LLM scriptés — aucun réseau.
"""

from __future__ import annotations

import datetime as dt

import pytest

from evals.lib.checks import run_checks
from evals.lib.models import EvalCase
from evals.lib.runner import UnsupportedProviderError, run_case


def _chat_case(**overrides):
    payload = {
        "id": "load-state",
        "kind": "chat",
        "scenario": "normal",
        "today": dt.date(2026, 4, 30),
        "user": "Où en est ma charge en ce moment ?",
        "stub": [
            {"tool_calls": [{"name": "get_training_load_state"}]},
            {"content": "Ta charge est stable cette semaine, rien à signaler."},
        ],
    }
    payload.update(overrides)
    return EvalCase(**payload)


def test_run_chat_case_calls_real_tools_without_network(tmp_path):
    envelope = run_case(_chat_case(), root=tmp_path / "run")
    assert envelope.case_id == "load-state"
    assert envelope.mode == "stub"
    assert envelope.stub_exhausted is False
    assert envelope.stub_calls == 2
    assert [entry["name"] for entry in envelope.tool_trace] == ["get_training_load_state"]
    assert envelope.tool_trace[0]["result"]["available"] is True
    assert envelope.tool_trace[0]["result"]["ctl"] is not None
    assert envelope.answer == "Ta charge est stable cette semaine, rien à signaler."
    assert "tool_call" in envelope.events
    assert "tool_result" in envelope.events
    assert envelope.events[-1] == "final"
    assert envelope.system_messages  # SYSTEM_PROMPT au minimum
    assert envelope.expectations["require_french"] is True


def test_run_chat_case_freezes_today(tmp_path):
    case = _chat_case(today=dt.date(2026, 4, 30))
    envelope = run_case(case, root=tmp_path / "run")
    # La courbe de charge est calculée jusqu'à la date figée du cas.
    assert envelope.tool_trace[0]["result"]["date"] == "2026-04-30"


def test_run_chat_case_detects_exhausted_stub(tmp_path):
    case = _chat_case(stub=[{"tool_calls": [{"name": "get_profile"}]}])
    envelope = run_case(case, root=tmp_path / "run")
    assert envelope.stub_exhausted is True
    assert envelope.answer == ""
    results = {result.name: result for result in run_checks(envelope)}
    assert not results["complete"].passed


def test_run_chat_case_provenance_check_on_scripted_answer(tmp_path):
    case = _chat_case(
        stub=[
            {"tool_calls": [{"name": "get_training_load_state"}]},
            {"content": "Ta CTL est de 999,9, tout va bien."},
        ],
    )
    envelope = run_case(case, root=tmp_path / "run")
    results = {result.name: result for result in run_checks(envelope)}
    assert not results["provenance"].passed
    assert "999" in results["provenance"].detail


def _plan_case(**overrides):
    payload = {
        "id": "plan-bad-week",
        "kind": "plan",
        "scenario": "normal",
        "today": dt.date(2026, 5, 25),
        "expectations": {"expect_adjustments": True},
        "plan": {
            "today": "2026-05-25",
            "target_date": "2026-06-01",
            "ctl_current": 60.0,
            "stub": [
                {
                    "workouts": [
                        {"date": f"2026-05-{day:02d}", "kind": "endurance", "duration_min": 45}
                        for day in range(25, 32)
                    ]
                }
            ],
        },
    }
    payload.update(overrides)
    return EvalCase(**payload)


def test_run_plan_case_applies_validator_guardrails(tmp_path):
    envelope = run_case(_plan_case(), root=tmp_path / "run")
    assert envelope.kind == "plan"
    assert envelope.plan is not None
    week = envelope.plan["weeks"][0]
    assert week["source"] == "llm"  # script valide, pas de fallback
    assert week["adjustments"]  # le validateur a corrigé la semaine absurde
    assert len(week["workouts"]) <= 6

    results = {result.name: result for result in run_checks(envelope)}
    assert results["plan.adjustments"].passed
    assert all(result.passed for result in results.values()), {
        name: result.detail for name, result in results.items() if not result.passed
    }


def test_run_plan_case_respects_availability(tmp_path):
    case = _plan_case(
        expectations={"expect_adjustments": True},
        plan={
            "today": "2026-05-25",
            "target_date": "2026-06-01",
            "ctl_current": 60.0,
            "availability": {
                "days": [
                    {"weekday": 0, "max_duration_min": 90, "context": "indoor"},
                    {"weekday": 2, "max_duration_min": 90, "context": "outdoor"},
                ],
            },
            "stub": [
                {
                    "workouts": [
                        {"date": "2026-05-26", "kind": "intervals", "duration_min": 90},
                        {"date": "2026-05-27", "kind": "intervals", "duration_min": 90},
                    ]
                }
            ],
        },
    )
    envelope = run_case(case, root=tmp_path / "run")
    assert envelope.plan is not None
    week = envelope.plan["weeks"][0]
    # Mardi n'est pas disponible : la séance doit être retirée.
    assert all(workout["date"] != "2026-05-26" for workout in week["workouts"])
    assert week["adjustments"]


def test_run_case_rejects_unknown_provider(tmp_path):
    with pytest.raises(UnsupportedProviderError, match="provider"):
        run_case(_chat_case(), root=tmp_path / "run", provider="magic")
