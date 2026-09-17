"""Routeur d'identité — comptes, invitations, sessions (palier 1a).

Auth par lien d'invitation + token de session opaque par utilisateur. Le token
clair n'est renvoyé qu'une seule fois (création d'invitation, acceptation).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from domestique_ai import security
from domestique_ai.api.deps import get_current_user, require_coach
from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import context_for_athlete
from domestique_ai.ingestion.db import init_db
from domestique_ai.platform_db import (
    InvitationError,
    accept_invitation,
    clear_failed_login,
    consume_reconnect_token,
    create_invitation,
    create_session,
    disable_totp,
    enable_totp,
    get_user_by_email,
    get_user_by_id,
    get_user_credentials,
    list_athletes_for_coach,
    list_invitations,
    list_recovery_codes,
    mark_recovery_code_used,
    record_failed_login,
    replace_recovery_codes,
    revoke_invitation,
    revoke_session,
    set_password,
    set_totp_secret,
    set_user_credentials,
    user_is_locked,
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
    email: str | None = None
    totp_enabled: bool = False


class InvitationCreate(BaseModel):
    role: Literal["coach", "athlete"] = "athlete"
    expires_in_days: int | None = Field(default=None, ge=1)


class InvitationCreated(BaseModel):
    role: str
    invite_token: str
    invite_url: str
    expires_at: str | None = None


class InvitationOut(BaseModel):
    id: int
    role: str
    status: str
    created_at: str
    accepted_at: str | None = None


class AcceptInvite(BaseModel):
    invite_token: str
    display_name: str | None = None
    email: str | None = None
    password: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    status: Literal["ok", "totp_required"]
    challenge: str | None = None
    session_token: str | None = None
    public_id: str | None = None
    role: str | None = None


class TotpLoginRequest(BaseModel):
    challenge: str
    code: str


class TotpEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg_data_uri: str


class TotpVerifyRequest(BaseModel):
    code: str


class TotpVerifyResponse(BaseModel):
    recovery_codes: list[str]


class PasswordConfirmRequest(BaseModel):
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class SetupCredentialsRequest(BaseModel):
    email: str
    password: str


class StatusResponse(BaseModel):
    status: str


class SessionTokenOut(BaseModel):
    session_token: str
    public_id: str
    role: str


class ReconnectRequest(BaseModel):
    token: str


class AthleteSummary(BaseModel):
    public_id: str
    display_name: str | None = None
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
            row = conn.execute("SELECT COUNT(*), MAX(date) FROM activities").fetchone()
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
        email=user.get("email"),
        totp_enabled=bool(user.get("totp_enabled")),
    )


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash factice pour égaliser le temps de réponse sur email inconnu (anti-énumération)."""
    return security.hash_password("domestique-ai-dummy-password")


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email ou mot de passe incorrect.",
    )


def _require_password(user_id: int, password: str) -> dict:
    """Vérifie le mot de passe courant (garde-fou des opérations sensibles)."""
    creds = get_user_credentials(user_id)
    if creds is None or not security.verify_password(creds["password_hash"], password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mot de passe incorrect.",
        )
    return creds


def _consume_recovery_code(user_id: int, code: str) -> bool:
    """Consomme un code de secours valide (usage unique). True si un code a matché."""
    for row in list_recovery_codes(user_id):
        if security.verify_recovery_code(row["code_hash"], code):
            mark_recovery_code_used(row["id"])
            return True
    return False


def _locked_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Trop de tentatives. Compte temporairement verrouillé.",
    )


def _issue_session(user: dict) -> LoginResponse:
    _session, token = create_session(user["id"])
    return LoginResponse(
        status="ok",
        session_token=token,
        public_id=user["public_id"],
        role=user["role"],
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    """Étape 1 : email + mot de passe. Renvoie un challenge 2FA si activée.

    Réponse 401 générique (pas d'énumération de comptes) que l'email soit inconnu
    ou le mot de passe faux.
    """
    user = get_user_by_email(body.email)
    if user is None:
        security.verify_password(_dummy_hash(), body.password)
        raise _invalid_credentials()

    creds = get_user_credentials(user["id"])
    if creds is None or not creds["password_hash"]:
        security.verify_password(_dummy_hash(), body.password)
        raise _invalid_credentials()

    if user_is_locked(creds["locked_until"]):
        raise _locked_error()

    if not security.verify_password(creds["password_hash"], body.password):
        record_failed_login(user["id"])
        raise _invalid_credentials()

    clear_failed_login(user["id"])

    if creds["totp_enabled"]:
        return LoginResponse(
            status="totp_required",
            challenge=security.create_login_challenge(user["id"]),
        )
    # 2FA pas encore enrôlée (transition) : session émise, le middleware impose
    # l'enrôlement avant tout autre appel.
    return _issue_session(user)


@router.post("/login/totp", response_model=LoginResponse)
def login_totp(body: TotpLoginRequest) -> LoginResponse:
    """Étape 2 : valide un code TOTP ou un code de secours contre le challenge."""
    user_id = security.verify_login_challenge(body.challenge)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Challenge expiré ou invalide.",
        )
    creds = get_user_credentials(user_id)
    user = get_user_by_id(user_id)
    if creds is None or user is None:
        raise _invalid_credentials()
    if user_is_locked(creds["locked_until"]):
        raise _locked_error()

    authenticated = security.verify_totp(creds["totp_secret"], body.code) or (
        _consume_recovery_code(user_id, body.code)
    )
    if not authenticated:
        record_failed_login(user_id)
        raise _invalid_credentials()

    clear_failed_login(user_id)
    return _issue_session(user)


@router.post("/invitations", response_model=InvitationCreated)
def create_invite(
    body: InvitationCreate,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> InvitationCreated:
    expires_at: str | None = None
    if body.expires_in_days:
        expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(days=body.expires_in_days)).isoformat()
    inv, token = create_invitation(created_by=coach["id"], role=body.role, expires_at=expires_at)
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
            id=i["id"],
            role=i["role"],
            status=i["status"],
            created_at=i["created_at"],
            accepted_at=i["accepted_at"],
        )
        for i in list_invitations(created_by=coach["id"])
    ]


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    invitation_id: int,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> None:
    """Révoque une invitation `pending` créée par le coach courant."""
    if not revoke_invitation(invitation_id, created_by=coach["id"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation introuvable ou déjà utilisée.",
        )


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
                last_activity_date=last_date,
                n_activities=n_activities,
            )
        )
    return out


@router.post("/accept-invite", response_model=SessionTokenOut)
def accept(body: AcceptInvite) -> SessionTokenOut:
    password_hash: str | None = None
    if body.password is not None:
        try:
            security.assert_password_strength(body.password)
        except security.PasswordPolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        password_hash = security.hash_password(body.password)
    try:
        user, token = accept_invitation(
            body.invite_token,
            display_name=body.display_name,
            email=body.email,
            password_hash=password_hash,
        )
    except InvitationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé par un autre compte.",
        ) from exc
    _provision_athlete_space(user)
    return SessionTokenOut(session_token=token, public_id=user["public_id"], role=user["role"])


# ---------------------------------------------------------------------------
# 2FA TOTP (authentifié) — enrôlement, vérification, désactivation
# ---------------------------------------------------------------------------


@router.post("/totp/enroll", response_model=TotpEnrollResponse)
def totp_enroll(user: dict = Depends(get_current_user)) -> TotpEnrollResponse:  # noqa: B008
    """Génère un secret TOTP non confirmé + QR à scanner. À confirmer via /totp/verify."""
    secret = security.new_totp_secret()
    set_totp_secret(user["id"], secret)
    account = user.get("email") or user["public_id"][:8]
    uri = security.totp_uri(secret, account)
    return TotpEnrollResponse(
        secret=secret,
        otpauth_uri=uri,
        qr_svg_data_uri=security.totp_qr_svg_data_uri(uri),
    )


@router.post("/totp/verify", response_model=TotpVerifyResponse)
def totp_verify(
    body: TotpVerifyRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> TotpVerifyResponse:
    """Confirme l'enrôlement (active la 2FA) et renvoie les codes de secours UNE fois."""
    creds = get_user_credentials(user["id"])
    if creds is None or not creds["totp_secret"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun enrôlement TOTP en cours. Appelle /totp/enroll d'abord.",
        )
    if not security.verify_totp(creds["totp_secret"], body.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code invalide.")
    enable_totp(user["id"])
    codes = security.generate_recovery_codes()
    replace_recovery_codes(user["id"], [security.hash_recovery_code(c) for c in codes])
    log.info("2FA activée pour %s", user["public_id"][:8])
    return TotpVerifyResponse(recovery_codes=codes)


@router.post("/totp/disable", response_model=StatusResponse)
def totp_disable(
    body: PasswordConfirmRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StatusResponse:
    """Désactive la 2FA (mot de passe requis) et purge les codes de secours."""
    _require_password(user["id"], body.password)
    disable_totp(user["id"])
    log.info("2FA désactivée pour %s", user["public_id"][:8])
    return StatusResponse(status="disabled")


# ---------------------------------------------------------------------------
# Mot de passe et codes de secours (authentifié)
# ---------------------------------------------------------------------------


@router.post("/setup-credentials", response_model=StatusResponse)
def setup_credentials(
    body: SetupCredentialsRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StatusResponse:
    """Définit email + mot de passe pour un compte encore sans identifiants.

    Sert aux sessions historiques (athlète sans mot de passe, coach bootstrap) :
    une fois les identifiants posés, l'enrôlement 2FA devient obligatoire.
    """
    if user.get("has_password"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Des identifiants sont déjà définis pour ce compte.",
        )
    try:
        security.assert_password_strength(body.password)
    except security.PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        set_user_credentials(user["id"], body.email, security.hash_password(body.password))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé par un autre compte.",
        ) from exc
    log.info("Identifiants définis pour %s", user["public_id"][:8])
    return StatusResponse(status="ok")


@router.post("/password", response_model=StatusResponse)
def change_password(
    body: PasswordChangeRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StatusResponse:
    """Change le mot de passe (mot de passe courant requis)."""
    _require_password(user["id"], body.current_password)
    try:
        security.assert_password_strength(body.new_password)
    except security.PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    set_password(user["id"], security.hash_password(body.new_password))
    log.info("Mot de passe changé pour %s", user["public_id"][:8])
    return StatusResponse(status="ok")


@router.post("/recovery-codes", response_model=TotpVerifyResponse)
def regenerate_recovery_codes(
    body: PasswordConfirmRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> TotpVerifyResponse:
    """Régénère les codes de secours (mot de passe requis, anciens purgés)."""
    _require_password(user["id"], body.password)
    codes = security.generate_recovery_codes()
    replace_recovery_codes(user["id"], [security.hash_recovery_code(c) for c in codes])
    return TotpVerifyResponse(recovery_codes=codes)


@router.post("/reconnect", response_model=SessionTokenOut)
def reconnect(body: ReconnectRequest) -> SessionTokenOut:
    """Échange un token de reconnexion (usage unique) contre une nouvelle session.

    Public (exempté du Bearer) : l'athlète déconnecté ouvre le lien fourni par
    son coach et récupère l'accès à SON compte existant.
    """
    user = consume_reconnect_token(body.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien de reconnexion invalide, expiré ou déjà utilisé.",
        )
    _session, token = create_session(user["id"])
    return SessionTokenOut(session_token=token, public_id=user["public_id"], role=user["role"])


@router.post("/logout")
def logout(
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        revoke_session(header[len(prefix) :].strip())
    return {"status": "logged_out"}
