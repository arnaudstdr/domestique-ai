"""Endpoints du coach LLM : sessions persistées + chat streamé en SSE."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    CoachAnalyzeRequest,
    CoachChatRequest,
    CoachMessage,
    CoachSession,
)
from domestique_ai.llm.coach import CoachReply, run_turn
from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
)
from domestique_ai.llm.ollama_client import OllamaError

router = APIRouter(prefix="/api/coach", tags=["coach"])
log = get_logger("coach")

# Cadence d'émission des tokens (synchrone côté run_turn — on simule).
_TOKEN_STREAM_DELAY_SEC = 0.02


def _sse_event(event_type: str, payload: dict[str, Any] | str) -> dict[str, str]:
    """Construit un événement SSE compatible sse-starlette."""
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"event": "message", "data": data, "id": event_type}


@router.get("/sessions", response_model=list[CoachSession])
def get_sessions(limit: int = 20) -> list[CoachSession]:
    """Liste des sessions persistées, plus récentes en premier."""
    sessions = list_sessions(limit=limit)
    return [
        CoachSession(
            session_id=s["session_id"],
            started_at=s["started_at"],
            messages=s["messages"],
            preview=(s.get("preview") or "")[:60],
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[CoachMessage])
def get_session_messages(session_id: str) -> list[CoachMessage]:
    """Renvoie tous les messages user / assistant d'une session."""
    raw = load_session(session_id)
    out: list[CoachMessage] = []
    for msg in raw:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content") or ""
        if not content.strip() and role == "user":
            continue
        out.append(
            CoachMessage(
                role=role,  # type: ignore[arg-type]
                content=content,
                thinking=msg.get("thinking"),
                tool_calls=msg.get("tool_calls"),
            )
        )
    return out


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_session(session_id: str) -> None:
    """Supprime une session et tous ses messages."""
    log.info("Suppression session %s", session_id[:8])
    delete_session(session_id)


def _tokenize(text: str) -> list[str]:
    """Découpe un texte en pseudo-tokens (mots + espaces préservés)."""
    if not text:
        return []
    tokens: list[str] = []
    buf = ""
    for char in text:
        buf += char
        if char in (" ", "\n", "\t"):
            tokens.append(buf)
            buf = ""
    if buf:
        tokens.append(buf)
    return tokens


async def _run_coach_turn(
    label: str,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> CoachReply:
    """Wrapper autour de `run_turn` avec logging détaillé.

    Émet trois traces : début, fin (avec stats), erreur (avec traceback). Le
    label permet de distinguer chat / analyze dans les logs.
    """
    start = time.perf_counter()
    log.info(
        "%s start | prompt_len=%d history=%d msgs",
        label,
        len(user_message),
        len(history or []),
    )
    try:
        reply = await asyncio.to_thread(run_turn, user_message, history)
    except OllamaError as exc:
        log.error("%s | Ollama error: %s", label, exc)
        raise
    except Exception:
        log.exception("%s | run_turn unhandled exception", label)
        raise

    duration = time.perf_counter() - start
    tool_names = [t.name for t in reply.tool_trace]
    log.info(
        "%s done | duration=%.1fs content=%d chars tools=%s thinking=%s",
        label,
        duration,
        len(reply.content or ""),
        tool_names or "[]",
        "yes" if reply.thinking else "no",
    )
    return reply


@router.post("/chat")
async def post_chat(payload: CoachChatRequest) -> EventSourceResponse:
    """Tour de conversation avec le coach, streamé en Server-Sent Events.

    Émet successivement :
    - session_id : id de la session (créé à la volée si absent)
    - thinking : contenu <think> (si dispo)
    - tool_call / tool_result pour chaque outil appelé
    - token : tokens de la réponse finale, mot à mot
    - done : fin du tour
    """
    if not (payload.message and payload.message.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message vide.",
        )

    session_id = payload.session_id or new_session_id()
    is_new_session = payload.session_id is None
    user_message = payload.message
    history = [
        m
        for m in load_session(session_id)
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    append_message(session_id, "user", {"role": "user", "content": user_message})
    label = f"chat[{session_id[:8]}{' new' if is_new_session else ''}]"

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        yield _sse_event(
            "session_id", {"type": "session_id", "value": session_id}
        )

        try:
            reply = await _run_coach_turn(label, user_message, history)
        except OllamaError as exc:
            yield _sse_event(
                "error",
                {"type": "error", "value": f"Ollama indisponible: {exc}"},
            )
            yield _sse_event("done", {"type": "done"})
            return
        except Exception as exc:  # noqa: BLE001 — on remonte tout au front
            yield _sse_event(
                "error",
                {"type": "error", "value": f"Coach erreur: {exc}"},
            )
            yield _sse_event("done", {"type": "done"})
            return

        if reply.thinking:
            yield _sse_event(
                "thinking",
                {"type": "thinking", "value": reply.thinking},
            )

        tool_trace_payload: list[dict[str, Any]] = []
        for trace in reply.tool_trace:
            tool_trace_payload.append(
                {
                    "name": trace.name,
                    "arguments": trace.arguments,
                    "result": trace.result,
                }
            )
            yield _sse_event(
                "tool_call",
                {
                    "type": "tool_call",
                    "name": trace.name,
                    "args": trace.arguments,
                },
            )
            yield _sse_event(
                "tool_result",
                {
                    "type": "tool_result",
                    "name": trace.name,
                    "result": trace.result,
                },
            )

        for token in _tokenize(reply.content):
            yield _sse_event(
                "token",
                {"type": "token", "value": token},
            )
            await asyncio.sleep(_TOKEN_STREAM_DELAY_SEC)

        append_message(
            session_id,
            "assistant",
            {
                "role": "assistant",
                "content": reply.content,
                "thinking": reply.thinking,
                "tool_calls": tool_trace_payload,
            },
        )

        yield _sse_event("done", {"type": "done"})

    return EventSourceResponse(event_stream())


@router.post("/analyze")
async def post_analyze(payload: CoachAnalyzeRequest) -> EventSourceResponse:
    """Analyse one-shot (sans persistance de session) — typiquement appelée
    depuis la page détail d'une activité.

    Émet les mêmes événements SSE que /chat sauf `session_id` : aucune
    conversation n'est créée en base, l'analyse reste éphémère côté serveur.
    """
    if not (payload.prompt and payload.prompt.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="prompt vide.",
        )

    prompt = payload.prompt
    label = "analyze"

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        try:
            reply = await _run_coach_turn(label, prompt)
        except OllamaError as exc:
            yield _sse_event(
                "error",
                {"type": "error", "value": f"Ollama indisponible: {exc}"},
            )
            yield _sse_event("done", {"type": "done"})
            return
        except Exception as exc:  # noqa: BLE001 — on remonte tout au front
            yield _sse_event(
                "error",
                {"type": "error", "value": f"Coach erreur: {exc}"},
            )
            yield _sse_event("done", {"type": "done"})
            return

        if reply.thinking:
            yield _sse_event(
                "thinking",
                {"type": "thinking", "value": reply.thinking},
            )

        for trace in reply.tool_trace:
            yield _sse_event(
                "tool_call",
                {
                    "type": "tool_call",
                    "name": trace.name,
                    "args": trace.arguments,
                },
            )
            yield _sse_event(
                "tool_result",
                {
                    "type": "tool_result",
                    "name": trace.name,
                    "result": trace.result,
                },
            )

        for token in _tokenize(reply.content):
            yield _sse_event(
                "token",
                {"type": "token", "value": token},
            )
            await asyncio.sleep(_TOKEN_STREAM_DELAY_SEC)

        yield _sse_event("done", {"type": "done"})

    return EventSourceResponse(event_stream())
