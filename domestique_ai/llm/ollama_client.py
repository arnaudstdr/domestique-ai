"""
Wrapper minimal autour du SDK officiel Ollama.

Deux entrypoints :
- `chat()` synchrone : utilisé par le dashboard Streamlit legacy via le
  wrapper sync `run_turn` de `coach.py`.
- `stream_chat()` async generator : utilisé par le path API (FastAPI), yield
  des chunks normalisés au fil de l'eau pour un vrai streaming SSE.

`think=False` est forcé partout : le modèle reste capable de raisonner en
interne, on coupe simplement l'émission du bloc verbeux <think> pour gagner
30-50 % du temps d'inférence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import ollama

from domestique_ai.config import get_ollama_host, get_ollama_model


class OllamaError(RuntimeError):
    """Erreur d'appel au backend Ollama (réseau, modèle indisponible, etc.)."""


def _sync_client() -> ollama.Client:
    host = get_ollama_host()
    return ollama.Client(host=host) if host else ollama.Client()


def _async_client() -> ollama.AsyncClient:
    host = get_ollama_host()
    return ollama.AsyncClient(host=host) if host else ollama.AsyncClient()


def chat(messages: list[dict[str, Any]],
         tools: list[dict[str, Any]] | None = None,
         model: str | None = None,
         options: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Appelle Ollama en mode bloquant et retourne le message brut.

    Le dict retourné a la forme {"role": "assistant", "content": str,
    "thinking": str | None, "tool_calls": list | None} et est directement
    réinjectable dans `messages` au tour suivant.
    """
    target_model = model or get_ollama_model()
    try:
        response = _sync_client().chat(
            model=target_model,
            messages=messages,
            tools=tools,
            think=False,
            options=options or {},
        )
    except ConnectionError as exc:
        raise OllamaError(
            f"Impossible de joindre Ollama (modèle {target_model}). "
            f"Vérifier que le service tourne. Détail: {exc}"
        ) from exc
    except ollama.ResponseError as exc:
        raise OllamaError(
            f"Ollama a refusé la requête (modèle {target_model}): {exc}"
        ) from exc

    msg = response.message
    return {
        "role": "assistant",
        "content": getattr(msg, "content", "") or "",
        "thinking": getattr(msg, "thinking", None),
        "tool_calls": [
            {
                "function": {
                    "name": tc.function.name,
                    "arguments": dict(tc.function.arguments or {}),
                },
            }
            for tc in (getattr(msg, "tool_calls", None) or [])
        ] or None,
    }


async def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield des chunks Ollama normalisés au fur et à mesure de la génération.

    Chaque chunk : `{"content": str, "thinking": str, "tool_calls": list|None,
    "done": bool}`. `content` et `thinking` sont des DELTAS incrémentaux.
    `tool_calls` arrive en bloc, en pratique sur le dernier chunk d'un tour.
    """
    target_model = model or get_ollama_model()
    try:
        stream = await _async_client().chat(
            model=target_model,
            messages=messages,
            tools=tools,
            stream=True,
            think=False,
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
