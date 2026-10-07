"""
Wrapper minimal autour du SDK officiel Ollama.

Entrypoints :
- `stream_chat()` : async generator de chunks normalisés (streaming SSE).
- `chat_structured()` : appel non-stream avec sortie JSON validable côté
  appelant (best-effort, ne lève jamais).
- `embed_texts()` : embeddings pour la mémoire du coach (best-effort).

Observabilité : chaque appel est enregistré (tokens, latence, statut, modèle)
via `llm.usage.record_llm_call()` — best-effort, jamais bloquant. Le paramètre
`label` identifie le *pourquoi* de l'appel (agrégé dans le panneau admin).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import ollama

from domestique_ai.config import (
    get_ollama_api_key,
    get_ollama_embed_model,
    get_ollama_host,
    get_ollama_model,
)
from domestique_ai.llm.usage import as_int, ns_to_ms, record_llm_call


class OllamaError(RuntimeError):
    """Erreur d'appel au backend Ollama (réseau, modèle indisponible, etc.)."""


def _async_client() -> ollama.AsyncClient:
    host = get_ollama_host()
    api_key = get_ollama_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    kwargs: dict[str, Any] = {}
    if host:
        kwargs["host"] = host
    if headers:
        kwargs["headers"] = headers
    return ollama.AsyncClient(**kwargs)


def _record(
    *,
    label: str | None,
    entrypoint: str,
    model: str,
    response: Any,
    status: str = "ok",
    error_type: str | None = None,
    tools_count: int = 0,
) -> None:
    """Extrait les métriques SDK d'une réponse et les persiste (best-effort)."""
    record_llm_call(
        label=label,
        entrypoint=entrypoint,
        model=model,
        prompt_tokens=as_int(getattr(response, "prompt_eval_count", None)),
        cached_tokens=as_int(getattr(response, "prompt_eval_cached_count", None)),
        completion_tokens=as_int(getattr(response, "eval_count", None)),
        total_duration_ms=ns_to_ms(getattr(response, "total_duration", None)),
        load_duration_ms=ns_to_ms(getattr(response, "load_duration", None)),
        eval_duration_ms=ns_to_ms(getattr(response, "eval_duration", None)),
        status=status,
        error_type=error_type,
        tools_count=tools_count,
    )


async def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    think: bool = False,
    options: dict[str, Any] | None = None,
    label: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield des chunks Ollama normalisés au fur et à mesure de la génération.

    Chaque chunk : `{"content": str, "thinking": str, "tool_calls": list|None,
    "done": bool}`. `content` et `thinking` sont des DELTAS incrémentaux.
    `tool_calls` arrive en bloc, en pratique sur le dernier chunk d'un tour.

    `think` contrôle l'émission du bloc de raisonnement. Sur gemma3/4, `think=False`
    rend le tool-calling moins fiable (le modèle saute les tools et répond
    de tête). Garder `think=True` au moins sur le 1ᵉʳ tour pour fiabiliser
    la décision d'appeler des tools.

    `label` identifie le type d'appel pour l'observabilité (table `llm_calls`).
    """
    target_model = model or get_ollama_model()
    final: Any = None
    tools_count = 0
    status = "ok"
    error_type: str | None = None
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
            chunk_tools = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": dict(tc.function.arguments or {}),
                    },
                }
                for tc in (getattr(msg, "tool_calls", None) or [])
            ]
            if chunk_tools:
                tools_count += len(chunk_tools)
            if bool(getattr(chunk, "done", False)):
                final = chunk
            yield {
                "content": getattr(msg, "content", "") or "",
                "thinking": getattr(msg, "thinking", "") or "",
                "tool_calls": chunk_tools or None,
                "done": bool(getattr(chunk, "done", False)),
            }
    except ConnectionError as exc:
        status, error_type = "error", "connection"
        raise OllamaError(
            f"Impossible de joindre Ollama (modèle {target_model}). "
            f"Vérifier que le service tourne. Détail: {exc}"
        ) from exc
    except ollama.ResponseError as exc:
        status, error_type = "error", "response"
        raise OllamaError(f"Ollama a refusé la requête (modèle {target_model}): {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — erreurs SDK variées (httpx, etc.)
        status, error_type = "error", "unexpected"
        raise OllamaError(
            f"Échec de l'appel Ollama (modèle {target_model}, {type(exc).__name__}): {exc}"
        ) from exc
    finally:
        _record(
            label=label,
            entrypoint="stream_chat",
            model=target_model,
            response=final,
            status=status,
            error_type=error_type,
            tools_count=tools_count,
        )


def extract_json_object(content: str | None) -> dict[str, Any] | None:
    """Extrait un objet JSON d'une réponse LLM, tolérant aux fences markdown.

    Les modèles cloud n'honorent pas toujours ``format=schema`` : ils peuvent
    entourer le JSON de ```json … ``` ou d'un préambule textuel. On tente le
    contenu brut, la version dé-fencée, puis le premier objet ``{…}`` équilibré.
    Retourne ``None`` si rien de parsable.
    """
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _structured_once(
    messages: list[dict[str, Any]],
    *,
    model: str,
    timeout_s: float,
    options: dict[str, Any] | None,
    fmt: Any,
    label: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Un appel contraint + parsing. Retourne ``(parsed, retryable)``.

    ``retryable`` vaut ``True`` quand un repli de format peut aider (contenu
    vide/non-JSON, ou refus Ollama) — jamais sur timeout/connexion (rejouer ne
    servirait à rien et gaspillerait du quota).
    """
    response: Any = None
    status = "ok"
    error_type: str | None = None
    parsed: dict[str, Any] | None = None
    retryable = False
    try:
        response = await asyncio.wait_for(
            _async_client().chat(
                model=model,
                messages=messages,
                stream=False,
                format=fmt,
                options=options or {},
            ),
            timeout=timeout_s,
        )
        content = getattr(getattr(response, "message", None), "content", None) or ""
        if not content.strip():
            status, error_type, retryable = "error", "empty", True
        else:
            parsed = extract_json_object(content)
            if parsed is None:
                status, error_type, retryable = "error", "parse", True
    except TimeoutError:
        status, error_type, retryable = "error", "timeout", False
    except ConnectionError:
        status, error_type, retryable = "error", "connection", False
    except ollama.ResponseError:
        status, error_type, retryable = "error", "response", True
    except Exception:  # noqa: BLE001 — best-effort, on retombe sur le fallback
        status, error_type, retryable = "error", "unexpected", False
    finally:
        _record(
            label=label,
            entrypoint="chat_structured",
            model=model,
            response=response,
            status=status,
            error_type=error_type,
        )
    return parsed, retryable


async def chat_structured(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    options: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    """Appel chat non-stream avec sortie JSON contrainte et parsing du résultat.

    Par défaut ``format="json"`` (JSON valide non contraint). Si ``schema`` est
    fourni (JSON Schema), il est passé en ``format`` au SDK : le décodage est
    alors contraint au schéma, ce qui réduit fortement les retries/fallbacks.
    Certains modèles cloud ignorant le schéma, un **unique repli en
    ``format="json"``** est tenté si la réponse au schéma n'est pas du JSON
    (coût borné à 2 appels).

    Retourne le dict parsé en cas de succès, ou ``None`` si :
    - Ollama est injoignable / refuse la requête,
    - le timeout est atteint,
    - le contenu retourné n'est pas un JSON valide.

    Cette fonction **ne lève jamais** : l'appelant choisit son fallback.
    """
    target_model = model or get_ollama_model()
    formats: list[Any] = [schema if schema is not None else "json"]
    if schema is not None:
        formats.append("json")

    for fmt in formats:
        parsed, retryable = await _structured_once(
            messages,
            model=target_model,
            timeout_s=timeout_s,
            options=options,
            fmt=fmt,
            label=label,
        )
        if parsed is not None:
            return parsed
        if not retryable:
            break
    return None


def chat_structured_sync(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    options: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    """Variante synchrone de ``chat_structured``, utilisable même sous event loop.

    Quand une boucle asyncio tourne déjà (cas du coach LLM appelé depuis un
    handler async), on exécute la coroutine dans un thread séparé pour ne pas
    se faire bloquer par ``asyncio.run`` qui refuse une boucle imbriquée.
    """

    async def _run() -> dict[str, Any] | None:
        return await chat_structured(
            messages,
            model=model,
            timeout_s=timeout_s,
            options=options,
            schema=schema,
            label=label,
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


async def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    label: str | None = None,
) -> list[list[float]]:
    """Calcule les embeddings d'une liste de textes via Ollama.

    Retourne une liste de vecteurs ``list[float]`` alignée sur ``texts``, ou
    ``[]`` en cas d'échec (Ollama injoignable, modèle absent, timeout). Cette
    fonction **ne lève jamais** : la mémoire du coach est best-effort.

    Modèle par défaut : ``get_ollama_embed_model()`` (``nomic-embed-text``),
    à tirer au préalable via ``ollama pull``.
    """
    if not texts:
        return []
    target_model = model or get_ollama_embed_model()
    response: Any = None
    status = "ok"
    error_type: str | None = None
    try:
        response = await asyncio.wait_for(
            _async_client().embed(model=target_model, input=texts),
            timeout=timeout_s,
        )
    except TimeoutError:
        status, error_type = "error", "timeout"
        return []
    except ConnectionError:
        status, error_type = "error", "connection"
        return []
    except ollama.ResponseError:
        status, error_type = "error", "response"
        return []
    except Exception:  # noqa: BLE001 — best-effort, on retombe sur "pas d'embedding"
        status, error_type = "error", "unexpected"
        return []
    finally:
        _record(
            label=label,
            entrypoint="embed_texts",
            model=target_model,
            response=response,
            status=status,
            error_type=error_type,
        )

    embeddings = getattr(response, "embeddings", None)
    if embeddings is None and isinstance(response, dict):
        embeddings = response.get("embeddings")
    if not embeddings:
        return []
    return [[float(x) for x in vec] for vec in embeddings]


def embed_texts_sync(
    texts: list[str],
    *,
    model: str | None = None,
    timeout_s: float = 30.0,
    label: str | None = None,
) -> list[list[float]]:
    """Variante synchrone de ``embed_texts``, sûre même sous event loop."""

    async def _run() -> list[list[float]]:
        return await embed_texts(texts, model=model, timeout_s=timeout_s, label=label)

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
