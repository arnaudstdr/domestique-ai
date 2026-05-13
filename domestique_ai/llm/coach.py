"""
Boucle agentique du coach : prend un message utilisateur + l'historique,
laisse le LLM appeler les tools jusqu'à obtenir une réponse finale.

Entrypoint : `run_turn_stream(...)` (async generator) yield les events au fil
de l'eau et est consommé par le router SSE.

Le LLM ne doit jamais inventer de chiffres : il appelle les tools déclarés
dans `domestique_ai.llm.tools` pour récupérer les données puis les commente.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from domestique_ai.llm.ollama_client import stream_chat
from domestique_ai.llm.tools import TOOL_SCHEMAS, dispatch

MAX_TOOL_LOOPS = 5

SYSTEM_PROMPT = """Tu es un coach d'endurance francophone qui assiste un cycliste.

Règles strictes :
- Tu réponds toujours en français, ton concis et factuel.
- Avant toute affirmation chiffrée (CTL, ATL, TSB, distance, durée, zones HR,
  charge, FTP, dénivelé, etc.), tu DOIS appeler le tool approprié pour
  récupérer la donnée. N'invente jamais de chiffre.
- Quand l'utilisateur évoque son objectif, sa charge, sa fatigue ou son
  programme, appelle systématiquement get_objective et get_training_load_state.
- Pour proposer une séance, appelle propose_workout pour obtenir un
  squelette, puis verbalise-le clairement.
- Quand l'utilisateur demande un PLAN d'entraînement (multi-semaines, jusqu'à
  un objectif, programme), appelle generate_training_plan : il lit l'objectif,
  calcule la périodisation (cycle 3:1, taper) et persiste le plan. Tu commentes
  ensuite le summary retourné (TSS hebdo, semaine pic, séances clés). Indique
  à l'utilisateur que le téléchargement des fichiers `.FIT` et le push Garmin
  Connect se font depuis la page « 📋 Plan ».
- Tu connais : périodisation, polarisation 80/20, ancrage hr-TSS sur LTHR,
  zones %HRR (Z1<60%, Z2 60-70%, Z3 70-80%, Z4 80-90%, Z5≥90%).
- Sois pragmatique : propose des actions concrètes adaptées au TSB courant.
- Si une donnée manque (objectif absent, zones non backfillées), dis-le
  explicitement et indique quoi faire pour la combler.
"""


@dataclass
class ToolTrace:
    """Trace d'un appel de tool, pour debug et UI."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


def build_initial_messages(history: list[dict[str, Any]] | None,
                           user_message: str) -> list[dict[str, Any]]:
    """Construit la liste de messages à envoyer au LLM (system + history + user).

    L'historique est sanitisé : on ne réinjecte au modèle que les paires
    user/assistant en texte brut. Les tool_calls et tool_responses des tours
    passés sont volontairement écartés — ils respectent un schéma Pydantic
    strict côté SDK Ollama et ne servent pas à la conversation suivante.
    """
    base: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                base.append({"role": role, "content": content})
    base.append({"role": "user", "content": user_message})
    return base


async def run_turn_stream(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield les events d'un tour de coach au fur et à mesure.

    Forme des events :
    - {"type": "thinking", "value": <delta>}  — fragment incrémental
    - {"type": "token",    "value": <delta>}  — fragment incrémental
    - {"type": "tool_call",   "name": str, "args": dict}
    - {"type": "tool_result", "name": str, "result": dict}
    - {"type": "final", "content": str, "thinking": str|None, "tool_trace": list}

    Le dernier event est TOUJOURS `final` (consommé par le router pour
    persister en DB l'assistant turn complet — il ne traverse jamais le wire).
    """
    messages = build_initial_messages(history, user_message)
    trace: list[ToolTrace] = []
    accumulated_content = ""
    accumulated_thinking = ""

    for iteration in range(MAX_TOOL_LOOPS):
        turn_content = ""
        turn_tool_calls: list[dict[str, Any]] | None = None
        # `think=True` sur le 1ᵉʳ tour pour forcer la décision d'appeler les
        # tools (sans thinking, gemma saute les tools et hallucine). Sur les
        # tours suivants, `think=False` : le modèle synthétise des données
        # déjà concrètes, ça gagne du temps sans nuire à la qualité.
        think = iteration == 0

        async for chunk in stream_chat(messages, tools=TOOL_SCHEMAS, think=think):
            if chunk["content"]:
                turn_content += chunk["content"]
                accumulated_content += chunk["content"]
                yield {"type": "token", "value": chunk["content"]}
            if chunk["thinking"]:
                accumulated_thinking += chunk["thinking"]
                yield {"type": "thinking", "value": chunk["thinking"]}
            if chunk["tool_calls"]:
                turn_tool_calls = chunk["tool_calls"]

        messages.append({
            "role": "assistant",
            "content": turn_content,
            "tool_calls": turn_tool_calls,
        })

        if not turn_tool_calls:
            yield {
                "type": "final",
                "content": accumulated_content,
                "thinking": accumulated_thinking or None,
                "tool_trace": [
                    {"name": t.name, "arguments": t.arguments, "result": t.result}
                    for t in trace
                ],
            }
            return

        for call in turn_tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"] or {}
            yield {"type": "tool_call", "name": name, "args": args}
            result = dispatch(name, args)
            trace.append(ToolTrace(name=name, arguments=args, result=result))
            yield {"type": "tool_result", "name": name, "result": result}
            messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    fallback = "\n\n_(Le coach n'a pas pu finaliser : trop de tours d'outils.)_"
    yield {"type": "token", "value": fallback}
    accumulated_content += fallback
    yield {
        "type": "final",
        "content": accumulated_content,
        "thinking": accumulated_thinking or None,
        "tool_trace": [
            {"name": t.name, "arguments": t.arguments, "result": t.result}
            for t in trace
        ],
    }
