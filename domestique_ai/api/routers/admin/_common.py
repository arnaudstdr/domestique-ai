"""Helpers et modèles partagés du panneau d'administration.

L'admin est volontairement **isolé** : il n'accède qu'à ``/api/admin/*`` et
n'hérite pas des droits coach. Le rôle ``admin`` n'est jamais auto-attribuable :
il se crée/promeut hors-ligne (``python -m domestique_ai.auth_cli …``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from domestique_ai import platform_db


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


class AdminLink(BaseModel):
    """Référence légère vers un autre compte (coach↔athlète)."""

    public_id: str
    display_name: str | None = None
    email: str | None = None


class AdminUserDetail(AdminUser):
    """Fiche compte complète, sans jamais exposer de secret."""

    locked: bool = False
    failed_attempts: int = 0
    locked_until: str | None = None
    password_changed_at: str | None = None
    last_activity_date: str | None = None
    n_activities: int = 0
    coaches: list[AdminLink] = []
    athletes_count: int = 0


class AdminSession(BaseModel):
    id: int
    created_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None


class PasswordResetResult(BaseModel):
    sent: bool


class RevokedSessions(BaseModel):
    revoked: int


class AdminAuditEntry(BaseModel):
    id: int
    actor_public_id: str | None = None
    action: str
    target_public_id: str | None = None
    details: Any = None
    created_at: str


class RoleUpdate(BaseModel):
    role: Literal["coach", "athlete", "admin"]


def to_admin_user(user: dict[str, Any]) -> AdminUser:
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


def to_admin_link(user: dict[str, Any]) -> AdminLink:
    return AdminLink(
        public_id=user["public_id"],
        display_name=user.get("display_name"),
        email=user.get("email"),
    )


def audit(
    actor: dict[str, Any],
    action: str,
    target: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Enregistre une action d'administration dans le journal d'audit."""
    platform_db.record_admin_audit(actor, action, target=target, details=details)
