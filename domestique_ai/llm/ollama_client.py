"""
Wrapper minimal autour du SDK officiel Ollama.

Centralise la création du client (host configurable via OLLAMA_HOST), la
sélection du modèle (OLLAMA_MODEL, défaut gemma4:31b-cloud) et l'appel
chat avec tool calling et mode thinking.
"""

from __future__ import annotations

from typing import Any

import ollama

from domestique_ai.config import get_ollama_host, get_ollama_model


class OllamaError(RuntimeError):
    """Erreur d'appel au backend Ollama (réseau, modèle indisponible, etc.)."""


def _client() -> ollama.Client:
    host = get_ollama_host()
    return ollama.Client(host=host) if host else ollama.Client()


def chat(messages: list[dict[str, Any]],
         tools: list[dict[str, Any]] | None = None,
         model: str | None = None,
         think: bool = True,
         options: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Appelle Ollama et retourne le message brut renvoyé par le modèle.

    Le dict retourné a la forme {"role": "assistant", "content": str,
    "thinking": str | None, "tool_calls": list | None} et est directement
    réinjectable dans `messages` au tour suivant.
    """
    target_model = model or get_ollama_model()
    try:
        response = _client().chat(
            model=target_model,
            messages=messages,
            tools=tools,
            think=think,
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
