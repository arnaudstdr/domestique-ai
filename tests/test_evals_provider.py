"""Tests du glue promptfoo (provider, assertion, générateur) — sans Node."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.assertions import get_assert
from evals.cases import generate_tests
from evals.coach_provider import call_api


def _write_case(directory: Path, case_id: str, *, answer: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": case_id,
        "title": f"Cas {case_id}",
        "scenario": "empty",
        "kind": "chat",
        "user": "Où en est ma charge ?",
        "stub": [
            {"tool_calls": [{"name": "get_profile"}]},
            {"content": answer},
        ],
    }
    (directory / f"{case_id}.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )


def _provider_inputs(case_id: str, tmp_path: Path) -> tuple[dict, dict]:
    options = {"config": {"cases_dir": str(tmp_path / "cases"), "workdir": str(tmp_path / "work")}}
    context = {"vars": {"case_id": case_id}}
    return options, context


def test_provider_returns_envelope_and_assertion_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_PROVIDER", raising=False)
    _write_case(tmp_path / "cases", "good", answer="Ta FTP est de 250 W, tout va bien.")
    options, context = _provider_inputs("good", tmp_path)
    response = call_api("good", options, context)
    assert "error" not in response
    envelope = response["output"]
    assert envelope["case_id"] == "good"
    assert envelope["tool_trace"][0]["name"] == "get_profile"
    assert envelope["mode"] == "stub"

    verdict = get_assert(envelope, {})
    assert verdict["pass"] is True
    assert "checks déterministes OK" in verdict["reason"]


def test_provider_assertion_fails_on_hallucination(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_PROVIDER", raising=False)
    _write_case(tmp_path / "cases", "bad", answer="Ta FTP est de 999 W, tout va bien.")
    options, context = _provider_inputs("bad", tmp_path)
    response = call_api("bad", options, context)
    verdict = get_assert(response["output"], {})
    assert verdict["pass"] is False
    assert "provenance" in verdict["reason"]


def test_provider_reports_unknown_case_as_error(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_PROVIDER", raising=False)
    options, context = _provider_inputs("missing", tmp_path)
    response = call_api("missing", options, context)
    assert "error" in response
    assert "missing" in response["error"]


def test_get_assert_accepts_json_string_output(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_PROVIDER", raising=False)
    _write_case(tmp_path / "cases", "good", answer="Ta FTP est de 250 W, tout va bien.")
    options, context = _provider_inputs("good", tmp_path)
    response = call_api("good", options, context)
    verdict = get_assert(json.dumps(response["output"]), {})
    assert verdict["pass"] is True


def test_generate_tests_emits_one_test_per_case(tmp_path):
    cases_dir = tmp_path / "cases"
    _write_case(cases_dir, "a", answer="Ta FTP est de 250 W.")
    _write_case(cases_dir, "b", answer="Ta FTP est de 250 W.")
    tests = generate_tests({"cases_dir": str(cases_dir)})
    assert [test["vars"]["case_id"] for test in tests] == ["a", "b"]
    assert tests[0]["assert"][0]["value"] == "file://assertions.py:get_assert"
    assert tests[0]["description"] == "Cas a"


def test_generate_tests_adds_judge_assertion_only_when_enabled(tmp_path, monkeypatch):
    cases_dir = tmp_path / "cases"
    _write_case(cases_dir, "a", answer="Ta FTP est de 250 W.")
    path = cases_dir / "a.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["judge"] = {"rubric": "La réponse est factuelle."}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    monkeypatch.delenv("EVAL_JUDGE", raising=False)
    assert len(generate_tests({"cases_dir": str(cases_dir)})[0]["assert"]) == 1

    monkeypatch.setenv("EVAL_JUDGE", "1")
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    assertions = generate_tests({"cases_dir": str(cases_dir)})[0]["assert"]
    assert [assertion["type"] for assertion in assertions] == ["python", "llm-rubric"]
    assert assertions[1]["provider"] == "ollama:chat:qwen2.5:7b"
    assert assertions[1]["value"] == "La réponse est factuelle."
