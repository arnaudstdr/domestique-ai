"""Invitations plateforme (tous coachs confondus)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit

router = APIRouter()


class AdminInvitation(BaseModel):
    id: int
    role: str
    status: str
    created_at: str | None = None
    expires_at: str | None = None
    accepted_at: str | None = None
    created_by_public_id: str | None = None
    created_by_email: str | None = None
    accepted_public_id: str | None = None


class AdminInvitationCreate(BaseModel):
    role: Literal["athlete", "coach"]
    expires_in_days: int | None = Field(default=None, ge=1, le=90)


class AdminInvitationCreated(BaseModel):
    invitation: AdminInvitation
    invite_url: str


def _enrich(inv: dict) -> AdminInvitation:
    creator = platform_db.get_user_by_id(inv["created_by"]) if inv.get("created_by") else None
    accepted = (
        platform_db.get_user_by_id(inv["accepted_user_id"]) if inv.get("accepted_user_id") else None
    )
    return AdminInvitation(
        id=inv["id"],
        role=inv["role"],
        status=inv["status"],
        created_at=inv.get("created_at"),
        expires_at=inv.get("expires_at"),
        accepted_at=inv.get("accepted_at"),
        created_by_public_id=(creator or {}).get("public_id"),
        created_by_email=(creator or {}).get("email"),
        accepted_public_id=(accepted or {}).get("public_id"),
    )


@router.get("/invitations", response_model=list[AdminInvitation])
def list_invitations() -> list[AdminInvitation]:
    """Toutes les invitations de la plateforme, plus récentes d'abord."""
    return [_enrich(inv) for inv in platform_db.list_invitations(created_by=None)]


@router.post(
    "/invitations", response_model=AdminInvitationCreated, status_code=status.HTTP_201_CREATED
)
def create_invitation(
    body: AdminInvitationCreate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminInvitationCreated:
    """Génère une invitation (athlète ou coach), lien à usage unique.

    L'acceptation (`/accept-invite`) est exemptée du gate d'inscription : le lien
    fonctionne même si l'inscription publique est désactivée. Le token clair n'est
    montré qu'ici.
    """
    expires_at: str | None = None
    if body.expires_in_days:
        expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(days=body.expires_in_days)).isoformat()
    inv, token = platform_db.create_invitation(
        created_by=admin["id"], role=body.role, expires_at=expires_at
    )
    audit(admin, "invitation_create", details={"invitation_id": inv["id"], "role": body.role})
    return AdminInvitationCreated(
        invitation=_enrich(inv),
        invite_url=f"/accept-invite?token={token}",
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    invitation_id: int,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> None:
    """Révoque une invitation en attente (toutes provenances)."""
    if not platform_db.revoke_invitation(invitation_id, created_by=None):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation introuvable ou déjà traitée.",
        )
    audit(admin, "invitation_revoke", details={"invitation_id": invitation_id})
