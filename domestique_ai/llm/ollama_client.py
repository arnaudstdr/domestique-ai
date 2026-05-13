"""
Wrapper minimal autour du SDK officiel Ollama.

Entrypoint : `stream_chat()` (async generator) yield des chunks normalisés au
fil de l'eau pour un vrai streaming SSE.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import ollama

from domestique_ai.config import get_ollama_host, get_ollama_model


class OllamaError(RuntimeError):
    """Erreur d'appel au backend Ollama (réseau, modèle indisponible, etc.)."""


def _async_client() -> ollama.AsyncClient:
    host = get_ollama_host()
    return ollama.AsyncClient(host=host) if host else ollama.AsyncClient()


async def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    think: bool = False,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield des chunks Ollama normalisés au fur et à mesure de la génération.

    Chaque chunk : `{"content": str, "thinking": str, "tool_calls": list|None,
    "done": bool}`. `content` et `thinking` sont des DELTAS incrémentaux.
    `tool_calls` arrive en bloc, en pratique sur le dernier chunk d'un tour.

    `think` contrôle l'émission du bloc <think>. Sur gemma3/4, `think=False`
    rend le tool-calling moins fiable (le modèle saute les tools et répond
    de tête). Garder `think=True` au moins sur le 1ᵉʳ tour pour fiabiliser
    la décision d'appeler des tools.
    """
    target_model = model or get_ollama_model()
    try:
        stream = await _async_client().chat(
            model=target_model,
            messages=messages,
            tools=tools,
            stream=True,
            think=think,
            options=options or {},
        )
        async for chunk in stream:
            msg = chunk.message
            yield {
                "content": getattr(msg, "content", "") or "",
                "thinking": getattr(msg, "thinking", "") or "",
                "tool_calls": [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": dict(tc.function.arguments or {}),
                        },
                    }
                    for tc in (getattr(msg, "tool_calls", None) or [])
                ] or None,
                "done": bool(getattr(chunk, "done", False)),
            }
    except ConnectionError as exc:
        raise OllamaError(
            f"Impossible de joindre Ollama (modèle {target_model}). "
            f"Vérifier que le service tourne. Détail: {exc}"
        ) from exc
    except ollama.ResponseError as exc:
        raise OllamaError(
            f"Ollama a refusé la requête (modèle {target_model}): {exc}"
        ) from exc
