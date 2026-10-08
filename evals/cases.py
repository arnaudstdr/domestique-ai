"""Générateur de tests promptfoo : un test par cas YAML de ``cases/``.

Le provider reçoit uniquement ``case_id`` (et recharge le cas), donc le YAML
reste la source de vérité, lisible et versionnée. Chaque test porte l'assertion
déterministe unique ; le mode live/juge ajoutera ses assertions en commit
ultérieur (via ``EVAL_JUDGE``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib.cases import load_cases  # noqa: E402

_DETERMINISTIC_ASSERTION: dict[str, Any] = {
    "type": "python",
    "value": "file://assertions.py:get_assert",
}


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Liste des tests promptfoo construits depuis les cas du dossier."""
    directory = Path((config or {}).get("cases_dir") or Path(__file__).parent / "cases")
    return [
        {
            "description": case.title or case.id,
            "vars": {"case_id": case.id},
            "assert": [dict(_DETERMINISTIC_ASSERTION)],
        }
        for case in load_cases(directory)
    ]
