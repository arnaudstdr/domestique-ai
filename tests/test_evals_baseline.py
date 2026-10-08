"""Tests de la baseline et de la détection de régression (``evals.lib.baseline``)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from evals import baseline_check
from evals.lib import prompts
from evals.lib.baseline import (
    BaselineError,
    build_baseline,
    compare,
    extract_case_success,
)


def _results(cases: dict[str, bool]) -> dict[str, Any]:
    return {
        "results": {
            "results": [
                {"testCase": {"vars": {"case_id": case_id}}, "success": ok}
                for case_id, ok in cases.items()
            ]
        }
    }


def test_extract_case_success_and_rejects_empty():
    assert extract_case_success(_results({"a": True, "b": False})) == {"a": True, "b": False}
    with pytest.raises(BaselineError, match="aucun cas"):
        extract_case_success({"results": {"results": []}})


def test_build_baseline_is_sorted_with_stats():
    baseline = build_baseline(_results({"b": False, "a": True}), prompt_sha="deadbeef")
    assert list(baseline["cases"]) == ["a", "b"]
    assert baseline["stats"] == {"successes": 1, "total": 2}
    assert baseline["prompt_sha"] == "deadbeef"


def test_compare_ok_when_identical():
    baseline = build_baseline(_results({"a": True, "b": False}), prompt_sha="sha")
    comparison = compare(_results({"a": True, "b": False}), baseline, prompt_sha="sha")
    assert comparison.ok
    assert "aucun écart" in comparison.summary()


def test_compare_detects_prompt_change():
    baseline = build_baseline(_results({"a": True}), prompt_sha="ancien")
    comparison = compare(_results({"a": True}), baseline, prompt_sha="nouveau")
    assert not comparison.ok
    assert comparison.prompt_changed
    assert "re-baseliner" in comparison.summary()


def test_compare_detects_regression_and_improvement():
    baseline = build_baseline(_results({"stable": True, "ko": False}), prompt_sha="sha")
    comparison = compare(
        _results({"stable": False, "ko": True}),
        baseline,
        prompt_sha="sha",
    )
    assert not comparison.ok
    assert comparison.regressions == ["stable"]
    assert comparison.improvements == ["ko"]


def test_compare_detects_missing_and_new_cases():
    baseline = build_baseline(_results({"ancien": True}), prompt_sha="sha")
    comparison = compare(_results({"nouveau": True}), baseline, prompt_sha="sha")
    assert not comparison.ok
    assert comparison.missing_cases == ["ancien"]
    assert comparison.new_cases == ["nouveau"]


def test_compare_rejects_unknown_schema():
    with pytest.raises(BaselineError, match="schema_version"):
        compare(_results({"a": True}), {"schema_version": 99, "cases": {"a": True}}, prompt_sha="x")


def test_main_updates_then_checks_ok(tmp_path, capsys):
    results_path = tmp_path / "results.json"
    baseline_path = tmp_path / "baseline.json"
    results_path.write_text(json.dumps(_results({"a": True})), encoding="utf-8")

    assert (
        baseline_check.main(
            ["--results", str(results_path), "--baseline", str(baseline_path), "--update"]
        )
        == 0
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["prompt_sha"] == prompts.compute_prompt_sha()

    assert (
        baseline_check.main(["--results", str(results_path), "--baseline", str(baseline_path)]) == 0
    )
    assert "aucun écart" in capsys.readouterr().out


def test_main_returns_1_on_regression(tmp_path, capsys):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(build_baseline(_results({"a": True}), prompt_sha=prompts.compute_prompt_sha())),
        encoding="utf-8",
    )
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps(_results({"a": False})), encoding="utf-8")

    assert (
        baseline_check.main(["--results", str(results_path), "--baseline", str(baseline_path)]) == 1
    )
    assert "régressions" in capsys.readouterr().out


def test_main_returns_2_on_unreadable_results(tmp_path, capsys):
    exit_code = baseline_check.main(
        ["--results", str(tmp_path / "absent.json"), "--baseline", str(tmp_path / "b.json")]
    )
    assert exit_code == 2
    assert "illisibles" in capsys.readouterr().err
