"""Routeur d'identité — comptes, invitations, sessions (palier 1a).

Auth par lien d'invitation + token de session opaque par utilisateur. Le token
clair n'est renvoyé qu'une seule fois (création d'invitation, acceptation).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from domestique_ai.api.deps import get_current_user, require_coach
from domestique_ai.platform_db import (
    InvitationError,
    accept_invitation,
    create_invitation,
    list_invitations,
    revoke_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MeResponse(BaseModel):
    public_id: str
    role: str
    display_name: str | None = None


class InvitationCreate(BaseModel):
    role: Literal["coach", "athlete"] = "athlete"
    expires_in_days: int | None = Field(default=None, ge=1)


class InvitationCreated(BaseModel):
    role: str
    invite_token: str
    invite_url: str
    expires_at: str | None = None


class InvitationOut(BaseModel):
    role: str
    status: str
    created_at: str
    accepted_at: str | None = None


class AcceptInvite(BaseModel):
    invite_token: str
    display_name: str | None = None


class SessionTokenOut(BaseModel):
    session_token: str
    public_id: str
    role: str


@router.get("/me", response_model=MeResponse)
def me(user: dict = Depends(get_current_user)) -> MeResponse:  # noqa: B008
    return MeResponse(
        public_id=user["public_id"],
        role=user["role"],
        display_name=user.get("display_name"),
    )


@router.post("/invitations", response_model=InvitationCreated)
def create_invite(
    body: InvitationCreate,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> InvitationCreated:
    expires_at: str | None = None
    if body.expires_in_days:
        expires_at = (
            dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(days=body.expires_in_days)
        ).isoformat()
    inv, token = create_invitation(
        created_by=coach["id"], role=body.role, expires_at=expires_at
    )
    return InvitationCreated(
        role=inv["role"],
        invite_token=token,
        invite_url=f"/accept-invite?token={token}",
        expires_at=inv["expires_at"],
    )


@router.get("/invitations", response_model=list[InvitationOut])
def list_invites(coach: dict = Depends(require_coach)) -> list[InvitationOut]:  # noqa: B008
    return [
        InvitationOut(
            role=i["role"],
            status=i["status"],
            created_at=i["created_at"],
            accepted_at=i["accepted_at"],
        )
        for i in list_invitations(created_by=coach["id"])
    ]


@router.post("/accept-invite", response_model=SessionTokenOut)
def accept(body: AcceptInvite) -> SessionTokenOut:
    try:
        user, token = accept_invitation(
            body.invite_token, display_name=body.display_name
        )
    except InvitationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return SessionTokenOut(
        session_token=token, public_id=user["public_id"], role=user["role"]
    )


@router.post("/logout")
def logout(
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        revoke_session(header[len(prefix):].strip())
    return {"status": "logged_out"}
