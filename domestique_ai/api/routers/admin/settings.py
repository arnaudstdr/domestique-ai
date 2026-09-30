"""Réglages plateforme (override runtime des variables d'env)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit

router = APIRouter()

_MAX_BROADCAST = 500


class AdminSettings(BaseModel):
    signup_enabled: bool
    maintenance_mode: bool = False
    broadcast_message: str | None = None


class SettingsUpdate(BaseModel):
    signup_enabled: bool | None = None
    maintenance_mode: bool | None = None
    broadcast_message: str | None = Field(default=None, max_length=_MAX_BROADCAST)


def _current_settings() -> AdminSettings:
    announcement = platform_db.get_announcement()
    return AdminSettings(
        signup_enabled=platform_db.effective_signup_enabled(),
        maintenance_mode=announcement["maintenance_mode"],
        broadcast_message=announcement["message"],
    )


@router.get("/settings", response_model=AdminSettings)
def get_settings() -> AdminSettings:
    """Réglages plateforme effectifs (override DB sinon variable d'env)."""
    return _current_settings()


@router.put("/settings", response_model=AdminSettings)
def update_settings(
    body: SettingsUpdate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminSettings:
    """Met à jour les réglages plateforme (override en base)."""
    data = body.model_dump(exclude_unset=True)
    if "signup_enabled" in data and data["signup_enabled"] is not None:
        platform_db.set_setting("signup_enabled", "1" if data["signup_enabled"] else "0")
    if "maintenance_mode" in data and data["maintenance_mode"] is not None:
        platform_db.set_setting("maintenance_mode", "1" if data["maintenance_mode"] else "0")
    if "broadcast_message" in data:
        message = (data["broadcast_message"] or "").strip()
        if len(message) > _MAX_BROADCAST:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Message trop long (max {_MAX_BROADCAST} caractères).",
            )
        platform_db.set_setting("broadcast_message", message or None)
    if data:
        audit(admin, "settings_update", details=data)
    return _current_settings()
