"""Gestion des comptes (liste, fiche, rôle, actions de sécurité)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import (
    AdminSession,
    AdminUser,
    AdminUserDetail,
    PasswordResetResult,
    RevokedSessions,
    RoleUpdate,
    audit,
    to_admin_link,
    to_admin_user,
)
from domestique_ai.api.routers.auth import issue_password_reset
from domestique_ai.athlete_context import activity_stats_for_user, remove_athlete_space

router = APIRouter()


def _get_target_or_404(public_id: str) -> dict:
    target = platform_db.get_user_by_public_id(public_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    return target


def _detail(user: dict) -> AdminUserDetail:
    security = platform_db.get_user_security_state(user["id"]) or {}
    n_activities, last_activity = activity_stats_for_user(user)
    coaches = (
        platform_db.list_coaches_for_athlete(user["id"]) if user.get("role") == "athlete" else []
    )
    athletes_count = (
        len(platform_db.list_athletes_for_coach(user["id"])) if user.get("role") == "coach" else 0
    )
    return AdminUserDetail(
        **to_admin_user(user).model_dump(),
        locked=platform_db.user_is_locked(security.get("locked_until")),
        failed_attempts=security.get("failed_attempts") or 0,
        locked_until=security.get("locked_until"),
        password_changed_at=security.get("password_changed_at"),
        last_activity_date=last_activity,
        n_activities=n_activities,
        coaches=[to_admin_link(c) for c in coaches],
        athletes_count=athletes_count,
    )


@router.get("/users", response_model=list[AdminUser])
def list_users() -> list[AdminUser]:
    """Liste tous les comptes de la plateforme."""
    return [to_admin_user(u) for u in platform_db.list_users()]


@router.get("/users/{public_id}", response_model=AdminUserDetail)
def get_user(public_id: str) -> AdminUserDetail:
    """Fiche détaillée d'un compte (sécurité, activité, liens) — sans secret."""
    return _detail(_get_target_or_404(public_id))


@router.post("/users/{public_id}/role", response_model=AdminUser)
def update_user_role(
    public_id: str,
    body: RoleUpdate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminUser:
    """Change le rôle d'un compte (promotion/rétrogradation admin comprise)."""
    target = _get_target_or_404(public_id)
    if target.get("is_bootstrap"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le rôle du compte propriétaire (bootstrap) ne peut pas être modifié.",
        )
    updated = platform_db.set_user_role(public_id, body.role)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    audit(admin, "role_change", target, {"from": target["role"], "to": body.role})
    return to_admin_user(updated)


@router.post("/users/{public_id}/reset-2fa", response_model=AdminUser)
def reset_user_2fa(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminUser:
    """Désactive la 2FA d'un compte (secret + codes de secours purgés).

    Le compte devra ré-enrôler un TOTP à la prochaine connexion (garde
    middleware). Utile quand un utilisateur a perdu son authenticator.
    """
    target = _get_target_or_404(public_id)
    platform_db.disable_totp(target["id"])
    updated = platform_db.get_user_by_public_id(public_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    audit(admin, "reset_2fa", target)
    return to_admin_user(updated)


@router.post("/users/{public_id}/unlock", response_model=AdminUserDetail)
def unlock_user(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminUserDetail:
    """Débloque un compte verrouillé après trop d'échecs de connexion."""
    target = _get_target_or_404(public_id)
    platform_db.clear_failed_login(target["id"])
    audit(admin, "unlock_account", target)
    return _detail(platform_db.get_user_by_public_id(public_id) or target)


@router.post("/users/{public_id}/verify-email", response_model=AdminUserDetail)
def verify_user_email(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminUserDetail:
    """Marque l'email d'un compte comme vérifié (dépannage)."""
    target = _get_target_or_404(public_id)
    platform_db.set_email_verified(target["id"], True)
    audit(admin, "verify_email", target)
    return _detail(platform_db.get_user_by_public_id(public_id) or target)


@router.post("/users/{public_id}/password-reset", response_model=PasswordResetResult)
def send_user_password_reset(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> PasswordResetResult:
    """Envoie un lien de réinitialisation de mot de passe au compte (email requis)."""
    target = _get_target_or_404(public_id)
    if not target.get("email"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce compte n'a pas d'email : impossible d'envoyer un lien.",
        )
    sent = issue_password_reset(target)
    audit(admin, "password_reset", target, {"sent": sent})
    return PasswordResetResult(sent=sent)


@router.get("/users/{public_id}/sessions", response_model=list[AdminSession])
def list_user_sessions(public_id: str) -> list[AdminSession]:
    """Sessions actives d'un compte (jamais le token)."""
    target = _get_target_or_404(public_id)
    return [AdminSession(**s) for s in platform_db.list_sessions(target["id"], active_only=True)]


@router.post("/users/{public_id}/logout", response_model=RevokedSessions)
def logout_user(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> RevokedSessions:
    """Révoque toutes les sessions d'un compte (déconnexion forcée)."""
    target = _get_target_or_404(public_id)
    revoked = platform_db.revoke_all_sessions(target["id"])
    audit(admin, "logout_all", target, {"revoked": revoked})
    return RevokedSessions(revoked=revoked)


@router.delete("/users/{public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    public_id: str,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> Response:
    """Supprime un compte et son espace de données (bootstrap refusé)."""
    target = _get_target_or_404(public_id)
    if target.get("is_bootstrap"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le compte propriétaire (bootstrap) ne peut pas être supprimé.",
        )
    # Audit AVANT la suppression (la FK ``target_user_id`` ne doit pas pointer
    # vers une ligne déjà effacée).
    audit(admin, "delete_account", target, {"email": target.get("email")})
    platform_db.delete_user(target["id"])
    remove_athlete_space(public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
