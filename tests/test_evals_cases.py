"""Tests du chargement des cas d'évaluation (``evals.lib.cases``)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import yaml

from evals.lib.cases import CaseLoadError, load_case, load_cases


def _write_case(path, payload: dict[str, Any]):
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def _chat_payload() -> dict[str, Any]:
    return {
        "id": "demo-chat",
        "title": "Cas de démonstration",
        "scenario": "empty",
        "kind": "chat",
        "user": "Bonjour",
        "stub": [{"content": "Bonjour !"}],
        "expectations": {"must_mention": ["Bonjour"]},
    }


def test_load_case_reads_yaml(tmp_path):
    path = _write_case(tmp_path / "demo.yaml", _chat_payload())
    case = load_case(path)
    assert case.id == "demo-chat"
    assert case.kind == "chat"
    assert case.stub[0].content == "Bonjour !"
    assert case.expectations.must_mention == ["Bonjour"]
    assert case.today == dt.date(2026, 4, 30)


def test_load_cases_sorted_and_deduped(tmp_path):
    payload = _chat_payload()
    payload["id"] = "a-first"
    _write_case(tmp_path / "a.yaml", payload)
    payload = _chat_payload()
    payload["id"] = "b-second"
    _write_case(tmp_path / "b.yaml", payload)
    cases = load_cases(tmp_path)
    assert [case.id for case in cases] == ["a-first", "b-second"]


def test_load_cases_rejects_duplicate_ids(tmp_path):
    _write_case(tmp_path / "a.yaml", _chat_payload())
    _write_case(tmp_path / "b.yaml", _chat_payload())
    with pytest.raises(CaseLoadError, match="dupliqué"):
        load_cases(tmp_path)


def test_load_cases_rejects_empty_dir(tmp_path):
    with pytest.raises(CaseLoadError, match="aucun cas"):
        load_cases(tmp_path)


def test_load_case_rejects_unknown_field(tmp_path):
    payload = _chat_payload()
    payload["inconnu"] = True
    path = _write_case(tmp_path / "demo.yaml", payload)
    with pytest.raises(CaseLoadError):
        load_case(path)


def test_load_case_requires_user_for_chat(tmp_path):
    payload = _chat_payload()
    payload.pop("user")
    path = _write_case(tmp_path / "demo.yaml", payload)
    with pytest.raises(CaseLoadError, match="user"):
        load_case(path)


def test_load_case_requires_plan_block_for_plan_kind(tmp_path):
    payload = _chat_payload()
    payload["kind"] = "plan"
    path = _write_case(tmp_path / "demo.yaml", payload)
    with pytest.raises(CaseLoadError, match="plan"):
        load_case(path)


def test_load_case_plan_kind_is_valid(tmp_path):
    payload = _chat_payload()
    payload["kind"] = "plan"
    payload.pop("user")
    payload.pop("stub")
    payload["plan"] = {
        "today": "2026-05-25",
        "target_date": "2026-06-01",
        "stub": [{"workouts": []}],
    }
    path = _write_case(tmp_path / "plan.yaml", payload)
    case = load_case(path)
    assert case.kind == "plan"
    assert case.plan is not None
    assert case.plan.today == dt.date(2026, 5, 25)
