"""Générateur de tests promptfoo : un test par cas YAML de ``cases/``.

Le provider reçoit uniquement ``case_id`` (et recharge le cas), donc le YAML
reste la source de vérité, lisible et versionnée. Chaque test porte l'assertion
déterministe (gate CI). En mode live/reporting, ``EVAL_JUDGE=1`` ajoute une
assertion ``llm-rubric`` servie par un modèle Ollama local — jamais le gate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib.cases import load_cases  # noqa: E402
from evals.lib.models import EvalCase  # noqa: E402

_DETERMINISTIC_ASSERTION: dict[str, Any] = {
    "type": "python",
    "value": "file://assertions.py:get_assert",
}
_DEFAULT_JUDGE_MODEL = "gemma4:31b-cloud"  # défaut de l'app (OLLAMA_MODEL le surcharge)


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Liste des tests promptfoo construits depuis les cas du dossier."""
    directory = Path((config or {}).get("cases_dir") or Path(__file__).parent / "cases")
    return [
        {
            "description": case.title or case.id,
            "vars": {"case_id": case.id},
            "assert": _assertions_for(case),
        }
        for case in load_cases(directory)
    ]


def _assertions_for(case: EvalCase) -> list[dict[str, Any]]:
    assertions = [dict(_DETERMINISTIC_ASSERTION)]
    if case.judge is not None and _judge_enabled():
        assertions.append(
            {
                "type": "llm-rubric",
                "value": case.judge.rubric,
                "provider": f"ollama:chat:{_judge_model(case)}",
            }
        )
    return assertions


def _judge_enabled() -> bool:
    return os.getenv("EVAL_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}


def _judge_model(case: EvalCase) -> str:
    explicit = case.judge.model if case.judge else None
    return (
        os.getenv("EVAL_JUDGE_MODEL")
        or explicit
        or os.getenv("OLLAMA_MODEL")
        or _DEFAULT_JUDGE_MODEL
    )
