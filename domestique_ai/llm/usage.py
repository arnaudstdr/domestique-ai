"""Observabilité des appels Ollama (usage tokens, latence, coût, erreurs).

Point d'entrée unique appelé par ``llm/ollama_client.py`` après chaque appel au
SDK : ``record_llm_call()`` persiste une ligne dans la table ``llm_calls`` de
``platform.db`` (data cross-tenant, comme le reste du panneau admin).

L'attribution à un athlète se fait via un ``ContextVar`` (``_llm_actor``) posé
aux points où l'identité est **connue** : le middleware d'auth (chemin requête) et
les boucles du scheduler (chemin tâche de fond). Le repo n'a aucun autre mécanisme
de contexte ambiant — l'``AthleteContext`` est transporté par argument, pas
stocké globalement.

Invariant : **best-effort absolu**. ``record_llm_call`` ne lève jamais (une panne
d'observabilité ne doit pas casser un appel LLM), et l'absence de contexte
(l'appel n'est pas rattaché à un utilisateur) est un cas normal → ``actor``
``NULL``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from domestique_ai.api.logging import get_logger

log = get_logger("llm.usage")

# Types d'appel au LLM — le « pourquoi » de chaque requête Ollama.
# Utilisé comme label d'agrégation dans le panneau admin.
COACH_CHAT = "coach_chat"
SESSION_TITLE = "session_title"
PLAN_WEEK = "plan_week"
SESSION_SUMMARY = "session_summary"
FACTS_EXTRACT = "facts_extract"
DAILY_BRIEF = "daily_brief"
WORKOUT_TODAY = "workout_today"
DECISION_REASON = "decision_reason"
WEEKLY_REVIEW_REASON = "weekly_review_reason"
EMBED_MESSAGES = "embed_messages"
EMBED_QUERY = "embed_query"
EMBED_FACT = "embed_fact"
EMBED_SUMMARY = "embed_summary"

# Libellés humains affichés dans le panneau admin.
LABELS: dict[str, str] = {
    COACH_CHAT: "Chat coach",
    SESSION_TITLE: "Titre de session",
    PLAN_WEEK: "Génération de plan (semaine)",
    SESSION_SUMMARY: "Résumé de session",
    FACTS_EXTRACT: "Extraction de faits",
    DAILY_BRIEF: "Brief quotidien",
    WORKOUT_TODAY: "Séance du jour",
    DECISION_REASON: "Raison décision matin",
    WEEKLY_REVIEW_REASON: "Raison revue hebdo",
    EMBED_MESSAGES: "Embeddings messages",
    EMBED_QUERY: "Embedding requête",
    EMBED_FACT: "Embedding fait",
    EMBED_SUMMARY: "Embedding résumé",
}


def label_human(label: str | None) -> str:
    """Libellé affichable pour un type d'appel (fallback : la valeur brute)."""
    if not label:
        return "—"
    return LABELS.get(label, label)


# ---------------------------------------------------------------------------
# Attribution (contextvar)
# ---------------------------------------------------------------------------

_llm_actor: ContextVar[str | None] = ContextVar("llm_actor", default=None)


def current_llm_actor() -> str | None:
    """``public_id`` de l'athlète auquel rattacher les appels LLM courants."""
    return _llm_actor.get()


@contextlib.contextmanager
def llm_attribution(public_id: str | None) -> Iterator[None]:
    """Pose le contexte d'attribution pour la durée du bloc ``with``.

    Posé au niveau du **middleware ASGI** (chemin requête) et des **boucles du
    scheduler** (chemin tâche de fond). Ne pas poser dans ``get_athlete_context``
    (dépendance *sync* exécutée dans un threadpool : le contextvar y serait perdu).
    """
    token = _llm_actor.set(public_id or None)
    try:
        yield
    finally:
        _llm_actor.reset(token)


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def record_llm_call(
    *,
    label: str | None,
    entrypoint: str,
    model: str | None,
    prompt_tokens: int | None = None,
    cached_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_duration_ms: float | None = None,
    load_duration_ms: float | None = None,
    eval_duration_ms: float | None = None,
    status: str = "ok",
    error_type: str | None = None,
    tools_count: int = 0,
    actor: str | None = None,
) -> None:
    """Persiste un appel Ollama (best-effort : ne lève jamais).

    ``actor`` par défaut = contexte d'attribution courant (``llm_attribution``).
    """
    try:
        from domestique_ai import platform_db

        platform_db.insert_llm_call(
            label=label,
            entrypoint=entrypoint,
            model=model,
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            total_duration_ms=total_duration_ms,
            load_duration_ms=load_duration_ms,
            eval_duration_ms=eval_duration_ms,
            status=status,
            error_type=error_type,
            tools_count=tools_count,
            actor_public_id=actor if actor is not None else current_llm_actor(),
        )
    except Exception:  # noqa: BLE001 — l'observabilité ne doit jamais casser un appel LLM
        log.exception("Enregistrement usage Ollama échoué (label=%s).", label)


def as_int(value: Any) -> int | None:
    """Convertit une valeur SDK en ``int`` (``None`` si absente/non numérique)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ns_to_ms(value: Any) -> float | None:
    """Nanosecondes → millisecondes (arrondi 0.1 ms), ``None`` si absent."""
    if value is None:
        return None
    try:
        return round(float(value) / 1_000_000, 1)
    except (TypeError, ValueError):
        return None


__all__ = [
    "COACH_CHAT",
    "DAILY_BRIEF",
    "DECISION_REASON",
    "EMBED_FACT",
    "EMBED_MESSAGES",
    "EMBED_QUERY",
    "EMBED_SUMMARY",
    "FACTS_EXTRACT",
    "LABELS",
    "PLAN_WEEK",
    "SESSION_SUMMARY",
    "SESSION_TITLE",
    "WEEKLY_REVIEW_REASON",
    "WORKOUT_TODAY",
    "as_int",
    "current_llm_actor",
    "label_human",
    "llm_attribution",
    "ns_to_ms",
    "record_llm_call",
]
