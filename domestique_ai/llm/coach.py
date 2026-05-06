"""
Boucle agentique du coach : prend un message utilisateur + l'historique,
laisse le LLM appeler les tools jusqu'à obtenir une réponse finale.

Le LLM ne doit jamais inventer de chiffres : il appelle les tools déclarés
dans `domestique_ai.llm.tools` pour récupérer les données puis les commente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from domestique_ai.llm.ollama_client import chat
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
  à l'utilisateur que le téléchargement des fichiers `.FIT` se fait depuis
  l'onglet « 📋 Plan » du dashboard.
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


@dataclass
class CoachReply:
    """Résultat d'un échange avec le coach."""

    content: str
    thinking: str | None = None
    tool_trace: list[ToolTrace] = field(default_factory=list)
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


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


def run_turn(user_message: str,
             history: list[dict[str, Any]] | None = None) -> CoachReply:
    """
    Exécute un tour de conversation : envoie le message, gère la boucle de
    tool calling, retourne la réponse finale + la trace.

    history contient les messages précédents (sans le system prompt, qui est
    rajouté à chaque tour pour rester explicite).
    """
    messages = build_initial_messages(history, user_message)
    trace: list[ToolTrace] = []
    final_thinking: str | None = None

    for _ in range(MAX_TOOL_LOOPS):
        response = chat(messages, tools=TOOL_SCHEMAS)
        if response.get("thinking"):
            final_thinking = response["thinking"]
        messages.append({
            "role": "assistant",
            "content": response.get("content", ""),
            "tool_calls": response.get("tool_calls"),
        })
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            return CoachReply(
                content=response.get("content", ""),
                thinking=final_thinking,
                tool_trace=trace,
                raw_messages=messages,
            )
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"] or {}
            result = dispatch(name, args)
            trace.append(ToolTrace(name=name, arguments=args, result=result))
            messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # Sortie de la boucle sans réponse finale : on renvoie le dernier contenu.
    last_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_content = msg["content"]
            break
    return CoachReply(
        content=last_content
        or "Le coach n'a pas pu finaliser sa réponse (trop de tours d'outils).",
        thinking=final_thinking,
        tool_trace=trace,
        raw_messages=messages,
    )
