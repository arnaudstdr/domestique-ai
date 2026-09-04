"""Endpoints du coach LLM : sessions persistées + chat streamé en SSE."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    CoachAnalyzeRequest,
    CoachChatRequest,
    CoachMessage,
    CoachSession,
    DailyBriefResponse,
    TodayWorkoutResponse,
    WorkoutSchema,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.llm.coach import run_turn_stream
from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    generate_session_title,
    get_session_title,
    list_sessions,
    load_session,
    new_session_id,
)
from domestique_ai.llm.daily_brief import build_daily_brief
from domestique_ai.llm.ollama_client import OllamaError

router = APIRouter(prefix="/api/coach", tags=["coach"])
log = get_logger("coach")


def _sse_event(event_type: str, payload: dict[str, Any] | str) -> dict[str, str]:
    """Construit un événement SSE compatible sse-starlette."""
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"event": "message", "data": data, "id": event_type}


@router.get("/sessions", response_model=list[CoachSession])
def get_sessions(
    limit: int = 20,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[CoachSession]:
    """Liste des sessions persistées, plus récentes en premier."""
    sessions = list_sessions(limit=limit, db_path=ctx.db_path)
    return [
        CoachSession(
            session_id=s["session_id"],
            started_at=s["started_at"],
            messages=s["messages"],
            preview=(s.get("preview") or "")[:60],
            title=s.get("title"),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[CoachMessage])
def get_session_messages(
    session_id: str,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[CoachMessage]:
    """Renvoie tous les messages user / assistant d'une session."""
    raw = load_session(session_id, db_path=ctx.db_path)
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
def remove_session(
    session_id: str,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> None:
    """Supprime une session et tous ses messages."""
    log.info("Suppression session %s", session_id[:8])
    delete_session(session_id, db_path=ctx.db_path)


async def _coach_event_stream(
    label: str,
    user_message: str,
    history: list[dict[str, Any]] | None,
    ctx: AthleteContext,
    *,
    session_id: str | None = None,
    persist_to_session: str | None = None,
) -> AsyncGenerator[dict[str, str], None]:
    """Pipeline SSE partagé entre `/chat` et `/analyze`.

    Émet successivement :
    - `session_id` (uniquement si fourni — premier tour d'une nouvelle session)
    - des deltas `thinking` à concaténer côté client
    - des paires `tool_call` / `tool_result` pour chaque outil appelé
    - des deltas `token` à concaténer côté client
    - `error` en cas de souci, puis `done` dans tous les cas
    """
    start = time.perf_counter()
    log.info(
        "%s start | prompt_len=%d history=%d msgs",
        label,
        len(user_message),
        len(history or []),
    )

    if session_id is not None:
        yield _sse_event("session_id", {"type": "session_id", "value": session_id})

    final_payload: dict[str, Any] | None = None
    try:
        async for ev in run_turn_stream(user_message, history, ctx=ctx):
            if ev["type"] == "final":
                final_payload = ev
                continue
            yield _sse_event(ev["type"], ev)
    except OllamaError as exc:
        log.error("%s | Ollama error: %s", label, exc)
        yield _sse_event("error", {"type": "error", "value": f"Ollama indisponible: {exc}"})
        yield _sse_event("done", {"type": "done"})
        return
    except Exception as exc:  # noqa: BLE001 — on remonte tout au front
        log.exception("%s | run_turn_stream unhandled exception", label)
        yield _sse_event("error", {"type": "error", "value": f"Coach erreur: {exc}"})
        yield _sse_event("done", {"type": "done"})
        return

    duration = time.perf_counter() - start
    if final_payload is not None:
        log.info(
            "%s done | duration=%.1fs content=%d chars tools=%s thinking=%s",
            label,
            duration,
            len(final_payload["content"] or ""),
            [t["name"] for t in final_payload["tool_trace"]] or "[]",
            "yes" if final_payload.get("thinking") else "no",
        )
        if persist_to_session is not None:
            append_message(
                persist_to_session,
                "assistant",
                {
                    "role": "assistant",
                    "content": final_payload["content"],
                    "thinking": final_payload["thinking"],
                    "tool_calls": final_payload["tool_trace"],
                },
                db_path=ctx.db_path,
            )
            # Génère un titre court en arrière-plan après le 1ᵉʳ échange.
            # `asyncio.create_task` ne bloque pas le yield "done" : l'utilisateur
            # voit sa réponse tout de suite, le titre arrive ~5 s plus tard via
            # le poll régulier de /api/coach/sessions côté front.
            if get_session_title(persist_to_session, db_path=ctx.db_path) is None:
                asyncio.create_task(_generate_title_safely(persist_to_session, ctx))

    yield _sse_event("done", {"type": "done"})


async def _generate_title_safely(session_id: str, ctx: AthleteContext) -> None:
    """Wrapper qui isole les erreurs de génération de titre (pas de remontée)."""
    try:
        title = await generate_session_title(session_id, db_path=ctx.db_path)
        if title:
            log.info("Titre session %s généré : %r", session_id[:8], title)
    except Exception:  # noqa: BLE001 — best-effort, on n'interrompt jamais le chat
        log.exception("Échec génération titre pour session %s", session_id[:8])


@router.post("/chat")
async def post_chat(
    payload: CoachChatRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> EventSourceResponse:
    """Tour de conversation avec le coach, streamé en Server-Sent Events.

    Changement sémantique vs. l'ancienne version : les events `thinking` et
    `token` sont désormais des DELTAS — le client doit les concaténer au lieu
    de remplacer. La valeur complète n'est jamais envoyée d'un coup.
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
        for m in load_session(session_id, db_path=ctx.db_path)
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    append_message(
        session_id,
        "user",
        {"role": "user", "content": user_message},
        db_path=ctx.db_path,
    )
    label = f"chat[{session_id[:8]}{' new' if is_new_session else ''}]"

    return EventSourceResponse(
        _coach_event_stream(
            label,
            user_message,
            history,
            ctx,
            session_id=session_id,
            persist_to_session=session_id,
        )
    )


@router.get("/daily-brief", response_model=DailyBriefResponse)
def get_daily_brief(
    refresh: bool = False,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> DailyBriefResponse:
    """Brief quotidien proactif : phrase de synthèse + état du jour.

    Agrège TSB, alerte saillante (overtraining + morning), séance suggérée,
    et génère une phrase courte via LLM (avec fallback déterministe si
    Ollama est injoignable). Cache journalier en mémoire — un seul appel
    LLM par jour et par état même si le Dashboard est rouvert plusieurs
    fois.
    """
    return DailyBriefResponse(**build_daily_brief(refresh=refresh, ctx=ctx))


@router.get("/today", response_model=TodayWorkoutResponse)
def get_today_workout(
    available_min: int | None = None,
    refresh: bool = False,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> TodayWorkoutResponse:
    """Séance suggérée pour aujourd'hui (TSB + objectif + plan + contexte).

    Croise objectif, TSB, plan persisté, dernière séance et distribution de
    zones de la semaine pour choisir le ``kind`` et la durée. Une couche LLM
    (Ollama, format JSON) gère le jugement fin avec fallback déterministe.
    Le résultat est mis en cache pour la journée (clé date + objectif + TSB
    arrondi). ``refresh=true`` force la régénération.
    """
    from domestique_ai.llm.daily_decision import evaluate_daily_decision
    from domestique_ai.processing.today import propose_workout_today

    result = propose_workout_today(
        available_min=available_min,
        refresh=refresh,
        ctx=ctx,
    )
    morning = evaluate_daily_decision(ctx=ctx, use_llm=False)
    base: dict[str, Any] = {}
    if result["rest_day"]:
        base.update(rest_day=True, reason=result.get("reason"))
    else:
        base.update(
            rest_day=False,
            workout=WorkoutSchema(**result["workout"]),
            tsb=result.get("tsb"),
            tsb_zone=result.get("tsb_zone"),
            rationale=result.get("rationale"),
            signals=result.get("signals"),
            source=result.get("source"),
        )
    base["morning_decision"] = morning.get("decision")
    base["morning_reason"] = morning.get("reason")
    base["morning_persisted"] = morning.get("persisted", False)
    return TodayWorkoutResponse(**base)


@router.post("/analyze")
async def post_analyze(
    payload: CoachAnalyzeRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> EventSourceResponse:
    """Analyse one-shot (sans persistance de session) — typiquement appelée
    depuis la page détail d'une activité.

    Émet les mêmes events SSE que `/chat` sauf `session_id` : aucune
    conversation n'est créée en base, l'analyse reste éphémère côté serveur.
    Les events `thinking` et `token` sont des DELTAS (à concaténer côté client).
    """
    if not (payload.prompt and payload.prompt.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="prompt vide.",
        )

    return EventSourceResponse(
        _coach_event_stream(
            "analyze",
            payload.prompt,
            None,
            ctx,
            session_id=None,
            persist_to_session=None,
        )
    )
