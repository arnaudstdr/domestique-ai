"""Panneau d'administration plateforme (réservé au rôle ``admin``).

L'admin est volontairement **isolé** : il n'accède qu'à ``/api/admin/*`` (gestion
des comptes, consultation des retours cross-tenant, réglages plateforme) et
n'hérite pas des droits coach. Le rôle ``admin`` n'est jamais auto-attribuable :
il se promeut hors-ligne (``python -m domestique_ai.auth_cli set-role admin``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class AdminUser(BaseModel):
    """Vue admin d'un compte (pas d'avatar ni de secret)."""

    public_id: str
    role: str
    display_name: str | None = None
    email: str | None = None
    email_verified: bool = False
    totp_enabled: bool = False
    has_password: bool = False
    is_bootstrap: bool = False
    created_at: str | None = None
    garmin_email: str | None = None
    has_garmin_credentials: bool = False


class RoleUpdate(BaseModel):
    role: Literal["coach", "athlete", "admin"]


class AdminSettings(BaseModel):
    signup_enabled: bool


class SettingsUpdate(BaseModel):
    signup_enabled: bool | None = None


class FeedbackStatusUpdate(BaseModel):
    status: Literal["new", "acknowledged", "done", "rejected"]


def _admin_user(user: dict[str, Any]) -> AdminUser:
    return AdminUser(
        public_id=user["public_id"],
        role=user["role"],
        display_name=user.get("display_name"),
        email=user.get("email"),
        email_verified=bool(user.get("email_verified")),
        totp_enabled=bool(user.get("totp_enabled")),
        has_password=bool(user.get("has_password")),
        is_bootstrap=bool(user.get("is_bootstrap")),
        created_at=user.get("created_at"),
        garmin_email=user.get("garmin_email"),
        has_garmin_credentials=bool(user.get("has_garmin_credentials")),
    )


@router.get("/users", response_model=list[AdminUser])
def list_users() -> list[AdminUser]:
    """Liste tous les comptes de la plateforme."""
    return [_admin_user(u) for u in platform_db.list_users()]


@router.post("/users/{public_id}/role", response_model=AdminUser)
def update_user_role(public_id: str, body: RoleUpdate) -> AdminUser:
    """Change le rôle d'un compte (promotion/rétrogradation admin comprise)."""
    target = platform_db.get_user_by_public_id(public_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    if target.get("is_bootstrap"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le rôle du compte propriétaire (bootstrap) ne peut pas être modifié.",
        )
    updated = platform_db.set_user_role(public_id, body.role)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    return _admin_user(updated)


@router.post("/users/{public_id}/reset-2fa", response_model=AdminUser)
def reset_user_2fa(public_id: str) -> AdminUser:
    """Désactive la 2FA d'un compte (secret + codes de secours purgés).

    Le compte devra ré-enrôler un TOTP à la prochaine connexion (garde
    middleware). Utile quand un utilisateur a perdu son authenticator.
    """
    target = platform_db.get_user_by_public_id(public_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    platform_db.disable_totp(target["id"])
    updated = platform_db.get_user_by_public_id(public_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    return _admin_user(updated)


@router.get("/feedback")
def list_feedback(limit: int | None = None) -> list[dict[str, Any]]:
    """Liste les retours testeurs (toutes tenants confondus), du plus récent au plus ancien."""
    return platform_db.list_feedback(limit=limit)


@router.patch("/feedback/{feedback_id}")
def update_feedback_status(feedback_id: int, body: FeedbackStatusUpdate) -> dict[str, Any]:
    """Change le statut de traitement d'un retour (``new``/``acknowledged``/``done``/``rejected``)."""
    updated = platform_db.set_feedback_status(feedback_id, body.status)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retour introuvable.")
    return updated


@router.get("/settings", response_model=AdminSettings)
def get_settings() -> AdminSettings:
    """Réglages plateforme effectifs (override DB sinon variable d'env)."""
    return AdminSettings(signup_enabled=platform_db.effective_signup_enabled())


@router.put("/settings", response_model=AdminSettings)
def update_settings(body: SettingsUpdate) -> AdminSettings:
    """Met à jour les réglages plateforme (override en base)."""
    if body.signup_enabled is not None:
        platform_db.set_setting("signup_enabled", "1" if body.signup_enabled else "0")
    return AdminSettings(signup_enabled=platform_db.effective_signup_enabled())
