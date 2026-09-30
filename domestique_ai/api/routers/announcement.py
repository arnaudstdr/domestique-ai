"""Annonce plateforme diffusée à tous les utilisateurs connectés (bandeau)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from domestique_ai import platform_db
from domestique_ai.api.deps import get_current_user

router = APIRouter(prefix="/api/announcement", tags=["announcement"])


class Announcement(BaseModel):
    maintenance_mode: bool = False
    message: str | None = None


@router.get("", response_model=Announcement)
def get_announcement(_user: dict = Depends(get_current_user)) -> Announcement:  # noqa: B008
    """Annonce courante (maintenance / message). Tout compte authentifié."""
    return Announcement(**platform_db.get_announcement())
