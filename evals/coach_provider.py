"""Provider promptfoo : exécute un cas d'évaluation et rend l'enveloppe complète.

Contrat promptfoo (``call_api``) :

- ``context["vars"]["case_id"]`` : identifiant du cas (émis par ``cases.py``) ;
- ``options["config"]["cases_dir"]`` : dossier des YAML de cas (défaut ``evals/cases``) ;
- ``options["config"]["workdir"]`` : racine des bases SQLite jetables (défaut temporaire) ;
- ``EVAL_PROVIDER`` : ``stub`` (défaut, aucun réseau) ou ``ollama`` (mode live local).

La sortie est le ``to_dict()`` de ``EvalEnvelope`` : réponse finale, trace des
tools, contexte injecté, attentes du cas. Les assertions n'ont donc besoin que
de l'output — aucune connaissance du pipeline côté promptfoo.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib.cases import load_case  # noqa: E402
from evals.lib.runner import run_case  # noqa: E402


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Exécute le cas et retourne l'enveloppe, ou ``{"error": ...}``."""
    try:
        case_id = context["vars"]["case_id"]
        config = options.get("config") or {}
        cases_dir = Path(config.get("cases_dir") or Path(__file__).parent / "cases")
        case = load_case(cases_dir / f"{case_id}.yaml")
        workdir = Path(config.get("workdir") or tempfile.mkdtemp(prefix="domestique-eval-"))
        provider = os.getenv("EVAL_PROVIDER", "stub")
        envelope = run_case(case, root=workdir / case_id, provider=provider)
        return {"output": envelope.to_dict()}
    except Exception as exc:  # noqa: BLE001 — frontière promptfoo : renvoyer l'erreur, pas crasher
        return {"error": f"{type(exc).__name__}: {exc}"}
