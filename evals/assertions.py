"""Assertions promptfoo : exécute la suite de checks déterministes du harnais.

Une seule assertion par cas : le détail (nom du check fautif + raison) est
rendu dans ``reason``, et le rapport markdown/JSON du harnais détaille chaque
check. Les checks sont définis dans ``evals/lib/checks.py`` (testés par pytest).

Fonction ``get_assert(output, context)`` exigée par promptfoo (``type: python``
avec ``value: file://assertions.py:get_assert``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib.checks import run_checks  # noqa: E402
from evals.lib.models import EvalEnvelope  # noqa: E402


def get_assert(output: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Rend ``{pass, score, reason}`` pour l'enveloppe produite par le provider."""
    envelope = EvalEnvelope(**json.loads(output) if isinstance(output, str) else output)
    results = run_checks(envelope)
    failures = [
        f"{result.name}: {result.detail}" if result.detail else result.name
        for result in results
        if not result.passed
    ]
    if failures:
        return {"pass": False, "score": 0.0, "reason": " | ".join(failures)}
    return {"pass": True, "score": 1.0, "reason": f"{len(results)} checks déterministes OK"}
