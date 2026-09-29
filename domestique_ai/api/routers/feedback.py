"""Formulaire de retour des testeurs (page ``/feedback``).

Le retour est persisté dans ``platform.db`` (data cross-tenant) et, si
``DOMESTIQUE_AI_FEEDBACK_EMAIL`` est défini, notifié par email — l'envoi est
best-effort et ne bloque jamais la réponse.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from domestique_ai import config
from domestique_ai.api.deps import get_current_user
from domestique_ai.api.schemas import FeedbackCreate, FeedbackCreated
from domestique_ai.mailer import send_feedback_notification
from domestique_ai.platform_db import insert_feedback
from domestique_ai.ratelimit import check as ratelimit_check

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Garde-fou anti-spam : 10 retours par heure et par utilisateur.
_MAX_PER_HOUR = 10


@router.post("", response_model=FeedbackCreated, status_code=status.HTTP_201_CREATED)
def submit_feedback(
    payload: FeedbackCreate,
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> FeedbackCreated:
    """Enregistre un retour testeur et notifie l'adresse configurée (best-effort)."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Le message ne peut pas être vide.",
        )
    if not ratelimit_check("feedback", user["public_id"], _MAX_PER_HOUR, 3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de retours envoyés, réessaie plus tard.",
        )
    entry = insert_feedback(
        category=payload.category,
        message=message,
        user_id=user["id"],
        public_id=user["public_id"],
        role=user.get("role"),
        author_email=user.get("email"),
        page=payload.page,
        app_version=payload.app_version,
        user_agent=request.headers.get("user-agent"),
    )
    notify_to = config.get_feedback_notify_email()
    if notify_to:
        try:
            send_feedback_notification(notify_to, entry)
        except Exception:
            log.exception("Notification de feedback échouée (id=%s)", entry["id"])
    return FeedbackCreated(id=entry["id"], created_at=entry["created_at"])
