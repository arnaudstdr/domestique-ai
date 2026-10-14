"""Gate locale : tous les cas dorés passent leurs checks déterministes.

Ce test est le pendant pytest du run promptfoo de la CI : il exécute chaque cas
en mode stub (aucun réseau) et exige 100 % de checks verts, y compris la
provenance numérique. Une régression de prompt n'est pas détectée ici (elle
l'est par ``tests/test_evals_sensitivity.py`` via l'empreinte de prompt).
"""

from __future__ import annotations

import pytest

from evals.lib.cases import load_cases
from evals.lib.checks import run_checks
from evals.lib.runner import run_case

_CASES = load_cases()


@pytest.mark.parametrize("case", _CASES, ids=[case.id for case in _CASES])
def test_golden_case_passes_deterministic_checks(case, tmp_path):
    envelope = run_case(case, root=tmp_path / case.id)
    failures = [
        f"{result.name}: {result.detail}" if result.detail else result.name
        for result in run_checks(envelope)
        if not result.passed
    ]
    assert not failures, "\n".join(failures)


def test_golden_suite_covers_sensitive_scenarios():
    scenarios = {case.scenario for case in _CASES}
    assert {"empty", "normal", "overtraining", "comeback", "busy_week"} <= scenarios
    kinds = {case.kind for case in _CASES}
    assert kinds == {"chat", "plan"}
    assert len(_CASES) >= 10
