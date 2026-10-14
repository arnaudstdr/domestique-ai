"""Empreinte des prompts et schémas exposés au LLM (sensibilité du gate).

Toute modification de prompt (coach, plan, brief) ou de ``TOOL_SCHEMAS``
change l'empreinte : le harnais exige alors une re-baseline explicite après
relecture du rapport, pour qu'une régression de prompt ne passe jamais
silencieusement. L'empreinte est versionnée dans ``evals/baseline.json``.
"""

from __future__ import annotations

import hashlib
import inspect
import json

from domestique_ai.llm import coach, daily_brief, plan_generator
from domestique_ai.llm.tools import TOOL_SCHEMAS


def prompt_parts() -> list[str]:
    """Sources qui déterminent le comportement textuel du LLM."""
    return [
        coach.SYSTEM_PROMPT,
        inspect.getsource(plan_generator._build_system_prompt),
        inspect.getsource(plan_generator._build_user_prompt),
        daily_brief._LLM_SYSTEM_PROMPT,
        json.dumps(TOOL_SCHEMAS, ensure_ascii=False, sort_keys=True),
    ]


def digest_parts(parts: list[str]) -> str:
    """SHA-256 tronqué (16 hex) d'une liste de sources."""
    payload = "\n<<<part>>>\n".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_prompt_sha() -> str:
    """Empreinte courante des prompts réellement utilisés par le coach."""
    return digest_parts(prompt_parts())
