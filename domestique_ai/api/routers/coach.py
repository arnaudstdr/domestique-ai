"""Endpoints du coach LLM : sessions persistées + chat streamé en SSE."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from domestique_ai.api.schemas import CoachChatRequest, CoachMessage, CoachSession
from domestique_ai.llm.coach import run_turn
from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
)
from domestique_ai.llm.ollama_client import OllamaError

router = APIRouter(prefix="/api/coach", tags=["coach"])

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
    user_message = payload.message
    history = [
        m
        for m in load_session(session_id)
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    append_message(session_id, "user", {"role": "user", "content": user_message})

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        yield _sse_event(
            "session_id", {"type": "session_id", "value": session_id}
        )

        try:
            reply = await asyncio.to_thread(run_turn, user_message, history)
        except OllamaError as exc:
            yield _sse_event(
                "error",
                {"type": "error", "value": f"Ollama indisponible: {exc}"},
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
