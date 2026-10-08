"""Tests de sensibilité : le harnais détecte une vraie dégradation.

Critère d'acceptation du chantier : une modification volontaire de prompt (ou
une sortie dégradée) doit faire échouer la comparaison au baseline — sans
dépendre d'un modèle réel, donc de façon 100 % déterministe en CI.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from domestique_ai.llm import coach
from evals.lib import prompts
from evals.lib.baseline import build_baseline, compare
from evals.lib.checks import run_checks
from evals.lib.models import EvalCase
from evals.lib.runner import run_case


def _results(cases: dict[str, bool]) -> dict[str, Any]:
    return {
        "results": {
            "results": [
                {"testCase": {"vars": {"case_id": case_id}}, "success": ok}
                for case_id, ok in cases.items()
            ]
        }
    }


def _chat_case(*, answer: str) -> EvalCase:
    return EvalCase(
        id="profile-check",
        kind="chat",
        scenario="empty",
        today=dt.date(2026, 4, 30),
        user="C'est quoi ma FTP ?",
        stub=[
            {"tool_calls": [{"name": "get_profile"}]},
            {"content": answer},
        ],
        expectations={"required_tools": ["get_profile"]},
    )


def test_prompt_mutation_is_detected_by_baseline_comparison(monkeypatch):
    baseline = build_baseline(
        _results({"profile-check": True}), prompt_sha=prompts.compute_prompt_sha()
    )
    assert compare(
        _results({"profile-check": True}), baseline, prompt_sha=prompts.compute_prompt_sha()
    ).ok

    monkeypatch.setattr(
        coach, "SYSTEM_PROMPT", coach.SYSTEM_PROMPT + "\n- appelle moins les tools."
    )
    mutated = compare(
        _results({"profile-check": True}), baseline, prompt_sha=prompts.compute_prompt_sha()
    )
    assert not mutated.ok
    assert mutated.prompt_changed


def test_degraded_answer_fails_checks_and_is_a_regression(tmp_path):
    good = _chat_case(answer="Ta FTP est de 250 W, d'après ton profil.")
    degraded = _chat_case(answer="Ta FTP est de 999 W, d'après ton profil.")

    good_envelope = run_case(good, root=tmp_path / "good")
    degraded_envelope = run_case(degraded, root=tmp_path / "degraded")

    assert all(result.passed for result in run_checks(good_envelope))
    failures = {result.name for result in run_checks(degraded_envelope) if not result.passed}
    assert failures == {"provenance"}

    baseline = build_baseline(
        _results({"profile-check": True}),
        prompt_sha=prompts.compute_prompt_sha(),
    )
    comparison = compare(
        _results({"profile-check": False}),
        baseline,
        prompt_sha=prompts.compute_prompt_sha(),
    )
    assert not comparison.ok
    assert comparison.regressions == ["profile-check"]


def test_missing_tool_call_is_detected_end_to_end(tmp_path):
    case = EvalCase(
        id="no-tool",
        kind="chat",
        scenario="empty",
        today=dt.date(2026, 4, 30),
        user="Donne-moi ma FTP.",
        stub=[{"content": "Ta FTP est de 250 W, d'après ton profil."}],
        expectations={"required_tools": ["get_profile"]},
    )
    envelope = run_case(case, root=tmp_path / "no-tool")
    failures = {result.name for result in run_checks(envelope) if not result.passed}
    assert "tools" in failures
