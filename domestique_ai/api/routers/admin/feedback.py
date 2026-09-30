"""Retours testeurs (data plateforme cross-tenant)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit

router = APIRouter()


class FeedbackStatusUpdate(BaseModel):
    status: Literal["new", "acknowledged", "done", "rejected"]


@router.get("/feedback")
def list_feedback(limit: int | None = None) -> list[dict[str, Any]]:
    """Liste les retours testeurs (tous tenants confondus), du plus récent au plus ancien."""
    return platform_db.list_feedback(limit=limit)


@router.patch("/feedback/{feedback_id}")
def update_feedback_status(
    feedback_id: int,
    body: FeedbackStatusUpdate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    """Change le statut de traitement d'un retour (``new``/``acknowledged``/``done``/``rejected``)."""
    updated = platform_db.set_feedback_status(feedback_id, body.status)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retour introuvable.")
    audit(admin, "feedback_status", details={"feedback_id": feedback_id, "status": body.status})
    return updated
