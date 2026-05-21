"""
Wrapper minimal autour du SDK officiel Ollama.

Entrypoints :
- `stream_chat()` : async generator de chunks normalisés (streaming SSE).
- `chat_structured()` : appel non-stream avec sortie JSON validable côté
  appelant (best-effort, ne lève jamais).
"""

from __future__ import annotations

import asyncio
import json
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


async def chat_structured(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Appel chat non-stream avec ``format="json"`` et parsing du résultat.

    Retourne le dict parsé en cas de succès, ou ``None`` si :
    - Ollama est injoignable / refuse la requête,
    - le timeout est atteint,
    - le contenu retourné n'est pas un JSON valide.

    Cette fonction **ne lève jamais** : l'appelant choisit son fallback.
    """
    target_model = model or get_ollama_model()
    try:
        response = await asyncio.wait_for(
            _async_client().chat(
                model=target_model,
                messages=messages,
                stream=False,
                format="json",
                options=options or {},
            ),
            timeout=timeout_s,
        )
    except (
        ConnectionError,
        ollama.ResponseError,
        asyncio.TimeoutError,
    ):
        return None
    except Exception:  # noqa: BLE001 — best-effort, on retombe sur le fallback
        return None

    content = getattr(getattr(response, "message", None), "content", None) or ""
    if not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def chat_structured_sync(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Variante synchrone de ``chat_structured``, utilisable même sous event loop.

    Quand une boucle asyncio tourne déjà (cas du coach LLM appelé depuis un
    handler async), on exécute la coroutine dans un thread séparé pour ne pas
    se faire bloquer par ``asyncio.run`` qui refuse une boucle imbriquée.
    """
    async def _run() -> dict[str, Any] | None:
        return await chat_structured(
            messages, model=model, timeout_s=timeout_s, options=options,
        )

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(_run())

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_run())).result()
