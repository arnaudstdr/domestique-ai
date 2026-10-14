"""Tests de l'empreinte de prompt (sensibilité du gate, ``evals.lib.prompts``)."""

from __future__ import annotations

from domestique_ai.llm import coach
from evals.lib import prompts


def test_prompt_sha_is_stable_and_hex():
    first = prompts.compute_prompt_sha()
    assert first == prompts.compute_prompt_sha()
    assert len(first) == 16
    assert all(char in "0123456789abcdef" for char in first)


def test_prompt_parts_include_coach_plan_and_tools():
    parts = prompts.prompt_parts()
    assert coach.SYSTEM_PROMPT in parts
    assert any("get_training_load_state" in part for part in parts)  # TOOL_SCHEMAS sérialisés


def test_prompt_sha_changes_when_prompt_is_mutated(monkeypatch):
    before = prompts.compute_prompt_sha()
    monkeypatch.setattr(coach, "SYSTEM_PROMPT", coach.SYSTEM_PROMPT + "\n- règle ajoutée")
    assert prompts.compute_prompt_sha() != before
