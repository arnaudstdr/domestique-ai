"""LLM scripté déterministe pour le mode stub (aucun réseau, aucun Ollama).

``ScriptedLLM`` remplace les deux points d'entrée du wrapper Ollama utilisés
par le coach et le générateur de plan :

- ``stream_chat`` : un tour scripté consommé par appel (chunks normalisés) ;
- ``chat_structured`` : une réponse JSON consommée par appel (``None`` accepté).

Une fois le script épuisé, l'appel suivant renvoie une réponse vide (le coach
finalise) et ``exhausted`` passe à ``True`` — le harnais le signale comme une
erreur de script, jamais comme un succès silencieux.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from evals.lib.models import StubTurn


class ScriptedLLM:
    """Rejoue des réponses scriptées et capture les appels reçus."""

    def __init__(
        self,
        *,
        turns: list[StubTurn] | None = None,
        structured: list[Any] | None = None,
    ) -> None:
        self._turns = list(turns or [])
        self._structured = list(structured or [])
        self.calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []
        self.exhausted = False

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        think: bool = False,
        options: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Itère les chunks du tour scripté suivant (forme normalisée Ollama)."""
        self.calls.append(
            {"messages": messages, "tools": tools, "think": think, "model": model, "label": label}
        )
        if not self._turns:
            self.exhausted = True
            yield {"content": "", "thinking": "", "tool_calls": None, "done": True}
            return
        turn = self._turns.pop(0)
        if turn.thinking:
            yield {"content": "", "thinking": turn.thinking, "tool_calls": None, "done": False}
        if turn.content:
            yield {"content": turn.content, "thinking": "", "tool_calls": None, "done": False}
        tool_calls = [
            {"function": {"name": call.name, "arguments": dict(call.arguments)}}
            for call in turn.tool_calls
        ]
        yield {"content": "", "thinking": "", "tool_calls": tool_calls or None, "done": True}

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        timeout_s: float = 30.0,
        options: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> dict[str, Any] | None:
        """Rend la réponse JSON scriptée suivante (``None`` si épuisé)."""
        self.structured_calls.append({"messages": messages, "schema": schema, "label": label})
        if not self._structured:
            self.exhausted = True
            return None
        return self._structured.pop(0)

    def system_messages(self) -> list[str]:
        """Contenu des messages système du 1ᵉʳ appel (corpus du contexte injecté)."""
        if not self.calls:
            return []
        return [
            str(message.get("content", ""))
            for message in self.calls[0]["messages"]
            if message.get("role") == "system"
        ]
