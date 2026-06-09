"""Routeur d'identité — comptes, invitations, sessions (palier 1a).

Auth par lien d'invitation + token de session opaque par utilisateur. Le token
clair n'est renvoyé qu'une seule fois (création d'invitation, acceptation).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from domestique_ai.api.deps import get_current_user, require_coach
from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import context_for_athlete
from domestique_ai.platform_db import (
    InvitationError,
    accept_invitation,
    create_invitation,
    list_athletes_for_coach,
    list_invitations,
    revoke_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger("auth")


def _provision_athlete_space(user: dict) -> None:
    """Crée le dossier + la DB activités d'un nouvel athlète. Idempotent, best-effort.

    Le user bootstrap n'a pas d'espace dédié (données legacy). Un échec d'I/O ne
    doit pas casser l'acceptation d'invitation : l'espace sera recréé à la volée
    au 1er accès (les fonctions de stockage font ``init_db`` + ``mkdir``).
    """
    if user.get("is_bootstrap"):
        return
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.ingestion.strava import init_db
    ctx = context_for_athlete(user)
    try:
        ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(ctx.db_path)
    except OSError:
        log.exception(
            "Provisioning espace athlète %s échoué (sera recréé à l'accès).",
            user["public_id"][:8],
        )


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


class AthleteSummary(BaseModel):
    public_id: str
    display_name: str | None = None
    strava_connected: bool
    last_activity_date: str | None = None
    n_activities: int = 0


def _athlete_activity_stats(db_path) -> tuple[int, str | None]:
    """(nombre d'activités, date ISO de la dernière) pour la DB d'un athlète.

    Best-effort : ``(0, None)`` si la DB n'existe pas encore (athlète jamais
    synchronisé) ou si la table ``activities`` est absente. On teste l'existence
    du fichier AVANT ``sqlite3.connect`` pour ne pas créer de DB vide.
    """
    if not db_path.exists():
        return 0, None
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(date) FROM activities"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0, None
    return (row[0] or 0), row[1]


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


@router.get("/athletes", response_model=list[AthleteSummary])
def list_athletes(coach: dict = Depends(require_coach)) -> list[AthleteSummary]:  # noqa: B008
    out: list[AthleteSummary] = []
    for athlete in list_athletes_for_coach(coach["id"]):
        ctx = context_for_athlete(athlete)
        n_activities, last_date = _athlete_activity_stats(ctx.db_path)
        out.append(
            AthleteSummary(
                public_id=athlete["public_id"],
                display_name=athlete.get("display_name"),
                strava_connected=ctx.tokens_path.exists(),
                last_activity_date=last_date,
                n_activities=n_activities,
            )
        )
    return out


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
    _provision_athlete_space(user)
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
