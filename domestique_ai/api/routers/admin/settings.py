"""Réglages plateforme (override runtime des variables d'env)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit

router = APIRouter()


class AdminSettings(BaseModel):
    signup_enabled: bool


class SettingsUpdate(BaseModel):
    signup_enabled: bool | None = None


@router.get("/settings", response_model=AdminSettings)
def get_settings() -> AdminSettings:
    """Réglages plateforme effectifs (override DB sinon variable d'env)."""
    return AdminSettings(signup_enabled=platform_db.effective_signup_enabled())


@router.put("/settings", response_model=AdminSettings)
def update_settings(
    body: SettingsUpdate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminSettings:
    """Met à jour les réglages plateforme (override en base)."""
    if body.signup_enabled is not None:
        platform_db.set_setting("signup_enabled", "1" if body.signup_enabled else "0")
        audit(admin, "settings_update", details={"signup_enabled": body.signup_enabled})
    return AdminSettings(signup_enabled=platform_db.effective_signup_enabled())
