"""Routeur d'identité — comptes, invitations, sessions (palier 1a).

Auth par lien d'invitation + token de session opaque par utilisateur. Le token
clair n'est renvoyé qu'une seule fois (création d'invitation, acceptation).
"""

from __future__ import annotations

import base64
import datetime as dt
import shutil
import sqlite3
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from domestique_ai import mailer, ratelimit, security
from domestique_ai.api.deps import get_current_user, require_coach
from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import context_for_athlete, remove_athlete_space
from domestique_ai.config import (
    garmin_token_dir_for,
    get_email_verification_ttl_hours,
    get_password_reset_ttl_minutes,
    google_health_tokens_path_for,
)
from domestique_ai.ingestion.db import init_db
from domestique_ai.legal import LEGAL_VERSION
from domestique_ai.platform_db import (
    InvitationError,
    accept_invitation,
    clear_failed_login,
    clear_user_garmin_credentials,
    consume_auth_token,
    consume_invitation_for_link,
    consume_reconnect_token,
    create_auth_token,
    create_invitation,
    create_session,
    create_user,
    delete_user,
    disable_totp,
    effective_signup_enabled,
    enable_totp,
    get_or_create_coach_invite_code,
    get_user_by_coach_invite_code,
    get_user_by_email,
    get_user_by_id,
    get_user_credentials,
    link_coach_athlete,
    list_athletes_for_coach,
    list_invitations,
    list_recovery_codes,
    mark_recovery_code_used,
    record_failed_login,
    replace_recovery_codes,
    revoke_all_sessions,
    revoke_invitation,
    revoke_session,
    rotate_coach_invite_code,
    set_email_verified,
    set_password,
    set_totp_secret,
    set_user_avatar,
    set_user_consents,
    set_user_credentials,
    set_user_onboarding,
    user_is_locked,
    withdraw_health_consent,
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


def _coach_invite_path(code: str) -> str:
    """Chemin de l'invitation réutilisable d'un coach (relatif, même origine)."""
    return f"/accept-invite?coach={code}"


def _send_verification_email(user: dict) -> None:
    """Génère un token de vérification et envoie le lien (best-effort)."""
    email = user.get("email")
    if not email:
        return
    expires = (
        dt.datetime.now(dt.UTC) + dt.timedelta(hours=get_email_verification_ttl_hours())
    ).isoformat()
    _row, token = create_auth_token(user["id"], "email_verify", expires)
    mailer.send_verification_email(email, token)


def issue_password_reset(user: dict) -> bool:
    """Génère un lien de réinitialisation et l'envoie par email.

    Partagé par ``forgot_password`` (anti-énumération) et le panneau admin
    (envoi explicite). ``False`` si le compte n'a pas d'email ou si l'envoi
    échoue (best-effort).
    """
    email = user.get("email")
    if not email:
        return False
    expires = (
        dt.datetime.now(dt.UTC) + dt.timedelta(minutes=get_password_reset_ttl_minutes())
    ).isoformat()
    _row, token = create_auth_token(user["id"], "password_reset", expires)
    return mailer.send_password_reset_email(email, token)


class MeResponse(BaseModel):
    public_id: str
    role: str
    display_name: str | None = None
    email: str | None = None
    totp_enabled: bool = False
    totp_grace_until: str | None = None
    avatar_url: str | None = None
    email_verified: bool = False
    has_password: bool = False
    is_bootstrap: bool = False
    terms_accepted_at: str | None = None
    terms_accepted_version: str | None = None
    health_consent_at: str | None = None
    health_consent_version: str | None = None
    health_consent_withdrawn_at: str | None = None
    onboarding_completed_at: str | None = None
    onboarding_dismissed_at: str | None = None


class OnboardingRequest(BaseModel):
    action: Literal["complete", "dismiss"]


class ConsentRequest(BaseModel):
    accepts_terms: bool = False
    accepts_health_data: bool = False


class SignupRequest(BaseModel):
    email: str
    password: str
    role: Literal["coach", "athlete"] = "athlete"
    display_name: str | None = None
    accepts_terms: bool = False
    accepts_health_data: bool = False


class SignupResponse(BaseModel):
    session_token: str
    public_id: str
    role: str
    invite_url: str | None = None
    email_verified: bool = False


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class CoachInviteLinkResponse(BaseModel):
    invite_url: str
    coach_code: str


class AuthConfigResponse(BaseModel):
    signup_enabled: bool
    legal_version: str


class LinkInviteRequest(BaseModel):
    invite_token: str | None = None
    coach_code: str | None = None


class DeleteAccountRequest(BaseModel):
    password: str | None = None
    code: str | None = None


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
    invite_token: str | None = None
    coach_code: str | None = None
    display_name: str | None = None
    email: str | None = None
    password: str | None = None
    accepts_terms: bool = False
    accepts_health_data: bool = False


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


class TotpEnrollRequest(BaseModel):
    password: str | None = None
    code: str | None = None


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
    avatar_url: str | None = None


# Plafond de la photo de profil reçue (data URL incluse). Le client redimensionne
# déjà à ~256 px (~20-30 Ko) ; ce plafond garde une marge et borne le stockage.
_AVATAR_MAX_BYTES = 500 * 1024

# Signatures des formats image acceptés (vérification des magic bytes — sans
# Pillow, on ne se fie ni au Content-Type ni au nom de fichier du client).
_AVATAR_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _decode_avatar(upload: UploadFile) -> str:
    """Valide un upload d'image et le convertit en data URL.

    Lève 422 si le contenu est trop volumineux, vide, ou d'un format non
    reconnu (magic bytes). Le format est déduit des octets, jamais du header
    client. WebP (RIFF....WEBP) est aussi accepté.
    """
    data = upload.file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Fichier image vide.",
        )
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image trop volumineuse (500 Ko maximum).",
        )
    mime = next((m for sig, m in _AVATAR_MAGIC if data.startswith(sig)), None)
    if mime is None and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    if mime is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Format d'image non reconnu (JPEG, PNG, GIF ou WebP).",
        )
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


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


def _consent_user_agent(request: Request) -> str | None:
    """User-Agent tronqué, conservé comme preuve de consentement (best-effort)."""
    return (request.headers.get("user-agent") or "").strip()[:500] or None


def _require_consents(accepts_terms: bool, accepts_health_data: bool) -> None:
    """Exige les deux consentements explicites (CGU/confidentialité + santé)."""
    if not accepts_terms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="L'acceptation des CGU et de la politique de confidentialité est requise.",
        )
    if not accepts_health_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Le traitement de tes données de santé nécessite ton consentement explicite.",
        )


def _me_response(user: dict) -> MeResponse:
    return MeResponse(
        public_id=user["public_id"],
        role=user["role"],
        display_name=user.get("display_name"),
        email=user.get("email"),
        totp_enabled=bool(user.get("totp_enabled")),
        totp_grace_until=user.get("totp_grace_until"),
        avatar_url=user.get("avatar"),
        email_verified=bool(user.get("email_verified")),
        has_password=bool(user.get("has_password")),
        is_bootstrap=bool(user.get("is_bootstrap")),
        terms_accepted_at=user.get("terms_accepted_at"),
        terms_accepted_version=user.get("terms_accepted_version"),
        health_consent_at=user.get("health_consent_at"),
        health_consent_version=user.get("health_consent_version"),
        health_consent_withdrawn_at=user.get("health_consent_withdrawn_at"),
        onboarding_completed_at=user.get("onboarding_completed_at"),
        onboarding_dismissed_at=user.get("onboarding_dismissed_at"),
    )


@router.get("/me", response_model=MeResponse)
def me(user: dict = Depends(get_current_user)) -> MeResponse:  # noqa: B008
    return _me_response(user)


@router.post("/me/onboarding", response_model=MeResponse)
def set_onboarding(
    body: OnboardingRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> MeResponse:
    """Marque le tuto d'onboarding du compte courant comme terminé ou passé.

    L'avancement des étapes est dérivé côté client des données réelles (profil,
    Garmin, santé) ; ce endpoint ne sert qu'à ne plus réafficher le guide.
    """
    set_user_onboarding(
        user["id"],
        completed=body.action == "complete",
        dismissed=body.action == "dismiss",
    )
    updated = get_user_by_id(user["id"])
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    log.info("Onboarding %s pour %s", body.action, user["public_id"][:8])
    return _me_response(updated)


@router.post("/me/consents", response_model=MeResponse)
def accept_consents(
    body: ConsentRequest,
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> MeResponse:
    """Enregistre les consentements d'un compte existant (ré-consentement).

    Utilisé par le portail de consentement affiché aux comptes créés avant la
    mise en conformité (et après toute nouvelle version des textes).
    """
    _require_consents(body.accepts_terms, body.accepts_health_data)
    set_user_consents(
        user["id"],
        terms_version=LEGAL_VERSION,
        health_consent_version=LEGAL_VERSION,
        user_agent=_consent_user_agent(request),
    )
    updated = get_user_by_id(user["id"])
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    log.info("Consentements enregistrés pour %s (v%s)", user["public_id"][:8], LEGAL_VERSION)
    return _me_response(updated)


@router.delete("/me/consents/health", response_model=MeResponse)
def withdraw_health(
    user: dict = Depends(get_current_user),  # noqa: B008
) -> MeResponse:
    """Retire le consentement au traitement des données de santé (art. 7.3 RGPD).

    Marque le retrait, déconnecte Garmin (credentials + tokens) et Google Health
    (tokens). Les données déjà collectées restent consultables ; leur effacement
    complet passe par la suppression du compte (``DELETE /me``) ou par la
    suppression de l'athlète par un coach/admin.
    """
    withdraw_health_consent(user["id"])
    clear_user_garmin_credentials(user["id"])
    ctx = context_for_athlete(user)
    shutil.rmtree(garmin_token_dir_for(ctx), ignore_errors=True)
    try:
        google_health_tokens_path_for(ctx).unlink(missing_ok=True)
    except OSError:
        log.warning(
            "Retrait consentement santé %s : tokens Google non supprimés.", user["public_id"][:8]
        )
    log.info("Consentement santé retiré pour %s", user["public_id"][:8])
    updated = get_user_by_id(user["id"])
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    return _me_response(updated)


class AvatarResponse(BaseModel):
    avatar_url: str | None = None


@router.put("/me/avatar", response_model=AvatarResponse)
def put_avatar(
    file: UploadFile = File(...),  # noqa: B008
    user: dict = Depends(get_current_user),  # noqa: B008
) -> AvatarResponse:
    """Pose/remplace la photo de profil du compte courant (multipart)."""
    avatar_url = _decode_avatar(file)
    set_user_avatar(user["id"], avatar_url)
    log.info("Photo de profil mise à jour pour %s", user["public_id"][:8])
    return AvatarResponse(avatar_url=avatar_url)


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
def delete_avatar(user: dict = Depends(get_current_user)) -> None:  # noqa: B008
    """Supprime la photo de profil du compte courant."""
    set_user_avatar(user["id"], None)
    log.info("Photo de profil supprimée pour %s", user["public_id"][:8])


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    body: DeleteAccountRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> None:
    """Supprime définitivement le compte courant et son espace de données.

    Confirmation forte : mot de passe (si le compte en a un) + code TOTP ou code
    de secours (si la 2FA est active). Le compte propriétaire (bootstrap) est
    protégé. Efface la ligne plateforme (sessions/invitations en cascade) puis le
    dossier de données ``data/athletes/<public_id>/``. Irréversible.
    """
    if user.get("is_bootstrap"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le compte propriétaire ne peut pas être supprimé.",
        )
    creds = get_user_credentials(user["id"])
    if creds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    if creds["password_hash"] and (
        not body.password or not security.verify_password(creds["password_hash"], body.password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mot de passe incorrect.",
        )
    if creds["totp_enabled"]:
        code = (body.code or "").strip()
        authenticated = bool(code) and (
            security.verify_totp(creds["totp_secret"], code)
            or _consume_recovery_code(user["id"], code)
        )
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Code de vérification incorrect.",
            )
    public_id = user["public_id"]
    try:
        deleted = delete_user(user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable.")
    remove_athlete_space(public_id)
    log.info("Compte %s supprimé par son propriétaire", public_id[:8])


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


def _require_totp_reauth(user: dict, body: TotpEnrollRequest | None) -> None:
    """Exige la ré-auth avant de remplacer une 2FA déjà active (vuln-0001).

    Mot de passe courant si le compte en a un, plus un code TOTP courant ou un
    code de secours. Empêche qu'un simple vol de session (XSS, token leak)
    remplace silencieusement le second facteur de la victime.
    """
    if body is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ré-authentification requise pour remplacer la 2FA active.",
        )
    if user.get("has_password"):
        _require_password(user["id"], body.password or "")
    creds = get_user_credentials(user["id"])
    code = (body.code or "").strip()
    code_ok = False
    if code:
        if creds and creds["totp_secret"]:
            code_ok = security.verify_totp(creds["totp_secret"], code)
        if not code_ok:
            code_ok = _consume_recovery_code(user["id"], code)
    if not code_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Code 2FA ou code de secours invalide.",
        )


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


# ---------------------------------------------------------------------------
# Inscription publique self-service + vérification d'email
# ---------------------------------------------------------------------------


def _rate_limited() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Trop de tentatives. Réessaie dans un moment.",
    )


def _require_password_policy(password: str) -> None:
    try:
        security.assert_password_strength(password)
    except security.PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    """Expose les capacités d'auth publiques (inscription ouverte, version légale)."""
    return AuthConfigResponse(
        signup_enabled=effective_signup_enabled(),
        legal_version=LEGAL_VERSION,
    )


@router.post("/signup", response_model=SignupResponse)
def signup(body: SignupRequest, request: Request) -> SignupResponse:
    """Inscription self-service (email + mot de passe + rôle).

    Désactivée par défaut (``DOMESTIQUE_AI_SIGNUP_ENABLED``). Un coach reçoit son
    lien d'invitation réutilisable. Le compte est créé non vérifié (email de
    confirmation envoyé, non bloquant).
    """
    if not effective_signup_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="L'inscription publique est désactivée.",
        )
    if not ratelimit.check(
        "signup_ip", ratelimit.client_ip(request), max_events=5, window_seconds=3600
    ):
        raise _rate_limited()
    _require_consents(body.accepts_terms, body.accepts_health_data)
    _require_password_policy(body.password)
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Email requis."
        )
    if get_user_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé par un autre compte.",
        )
    try:
        user = create_user(
            role=body.role,
            display_name=(body.display_name or "").strip() or None,
            email=email,
            password_hash=security.hash_password(body.password),
            email_verified=False,
            terms_version=LEGAL_VERSION,
            health_consent_version=LEGAL_VERSION,
            consent_user_agent=_consent_user_agent(request),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé par un autre compte.",
        ) from exc
    _provision_athlete_space(user)
    invite_url: str | None = None
    if body.role == "coach":
        invite_url = _coach_invite_path(get_or_create_coach_invite_code(user["id"]))
    _send_verification_email(user)
    _session, token = create_session(user["id"])
    log.info("Inscription %s (%s)", user["public_id"][:8], body.role)
    return SignupResponse(
        session_token=token,
        public_id=user["public_id"],
        role=user["role"],
        invite_url=invite_url,
        email_verified=False,
    )


@router.post("/verify-email", response_model=StatusResponse)
def verify_email(body: VerifyEmailRequest) -> StatusResponse:
    """Consomme un token de vérification d'email (usage unique)."""
    user = consume_auth_token(body.token, "email_verify")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien de vérification invalide ou expiré.",
        )
    set_email_verified(user["id"], True)
    log.info("Email vérifié pour %s", user["public_id"][:8])
    return StatusResponse(status="verified")


@router.post("/resend-verification", response_model=StatusResponse)
def resend_verification(
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StatusResponse:
    """Renvoye l'email de vérification du compte courant (rate-limité)."""
    if user.get("email_verified"):
        return StatusResponse(status="already_verified")
    if not ratelimit.check("resend_user", str(user["id"]), max_events=3, window_seconds=3600):
        raise _rate_limited()
    _send_verification_email(user)
    return StatusResponse(status="sent")


# ---------------------------------------------------------------------------
# Mot de passe oublié (public, token à usage unique)
# ---------------------------------------------------------------------------


@router.post("/forgot-password", response_model=StatusResponse)
def forgot_password(body: ForgotPasswordRequest, request: Request) -> StatusResponse:
    """Demande un lien de réinitialisation. Répond 200 même si l'email est inconnu.

    Anti-énumération : on ne distingue jamais un compte existant d'un email
    inconnu. Rate-limité par IP (large) et par email (strict).
    """
    ip = ratelimit.client_ip(request)
    if not ratelimit.check("forgot_ip", ip, max_events=10, window_seconds=3600):
        raise _rate_limited()
    email = (body.email or "").strip().lower()
    if email and ratelimit.check("forgot_email", email, max_events=3, window_seconds=3600):
        user = get_user_by_email(email)
        if user is not None and user.get("email"):
            issue_password_reset(user)
    return StatusResponse(status="ok")


@router.post("/reset-password", response_model=StatusResponse)
def reset_password(body: ResetPasswordRequest) -> StatusResponse:
    """Consomme un token de reset : nouveau mot de passe + déconnexion globale.

    Marque aussi l'email vérifié (le clic prouve la possession de l'adresse).
    Ne touche pas au TOTP : si la 2FA est perdue, les codes de secours ou la CLI
    restent les voies de récupération.
    """
    _require_password_policy(body.new_password)
    user = consume_auth_token(body.token, "password_reset")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien de réinitialisation invalide ou expiré.",
        )
    set_password(user["id"], security.hash_password(body.new_password))
    set_email_verified(user["id"], True)
    revoked = revoke_all_sessions(user["id"])
    log.info(
        "Mot de passe réinitialisé pour %s (%d session(s) révoquée(s))",
        user["public_id"][:8],
        revoked,
    )
    return StatusResponse(status="reset")


# ---------------------------------------------------------------------------
# Lien d'invitation réutilisable du coach
# ---------------------------------------------------------------------------


@router.get("/coach-invite-link", response_model=CoachInviteLinkResponse)
def coach_invite_link(coach: dict = Depends(require_coach)) -> CoachInviteLinkResponse:  # noqa: B008
    """Retourne (et crée au besoin) le lien d'invitation réutilisable du coach."""
    code = get_or_create_coach_invite_code(coach["id"])
    return CoachInviteLinkResponse(invite_url=_coach_invite_path(code), coach_code=code)


@router.post("/coach-invite-link/rotate", response_model=CoachInviteLinkResponse)
def rotate_coach_link(coach: dict = Depends(require_coach)) -> CoachInviteLinkResponse:  # noqa: B008
    """Régénère le lien réutilisable (révoque l'ancien : les anciens liens meurent)."""
    code = rotate_coach_invite_code(coach["id"])
    log.info("Lien d'invitation régénéré pour coach %s", coach["public_id"][:8])
    return CoachInviteLinkResponse(invite_url=_coach_invite_path(code), coach_code=code)


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
                avatar_url=athlete.get("avatar"),
            )
        )
    return out


@router.post("/accept-invite", response_model=SessionTokenOut)
def accept(body: AcceptInvite, request: Request) -> SessionTokenOut:
    """Crée un compte via une invitation (token à usage unique ou code coach).

    Si l'email est déjà pris, le client doit basculer sur ``/accept-invite/link``
    après connexion (on ne crée jamais de doublon d'athlète). Les consentements
    (CGU/confidentialité + données de santé) sont exigés comme à l'inscription.
    """
    _require_consents(body.accepts_terms, body.accepts_health_data)
    password_hash: str | None = None
    if body.password is not None:
        try:
            security.assert_password_strength(body.password)
        except security.PasswordPolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        password_hash = security.hash_password(body.password)

    # Code d'invitation réutilisable d'un coach : crée l'athlète + le lien.
    if body.coach_code:
        coach = get_user_by_coach_invite_code(body.coach_code)
        if coach is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lien d'invitation invalide.",
            )
        email = (body.email or "").strip().lower()
        if email and get_user_by_email(email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cet email est déjà utilisé. Connecte-toi pour rejoindre ce coach.",
            )
        try:
            user = create_user(
                role="athlete",
                display_name=(body.display_name or "").strip() or None,
                email=email or None,
                password_hash=password_hash,
                email_verified=True,
                terms_version=LEGAL_VERSION,
                health_consent_version=LEGAL_VERSION,
                consent_user_agent=_consent_user_agent(request),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cet email est déjà utilisé. Connecte-toi pour rejoindre ce coach.",
            ) from exc
        link_coach_athlete(coach["id"], user["id"])
        _provision_athlete_space(user)
        _session, token = create_session(user["id"])
        return SessionTokenOut(session_token=token, public_id=user["public_id"], role=user["role"])

    if not body.invite_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invite_token ou coach_code requis.",
        )
    try:
        user, token = accept_invitation(
            body.invite_token,
            display_name=body.display_name,
            email=body.email,
            password_hash=password_hash,
            terms_version=LEGAL_VERSION,
            health_consent_version=LEGAL_VERSION,
            consent_user_agent=_consent_user_agent(request),
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


@router.post("/accept-invite/link", response_model=StatusResponse)
def accept_invite_link(
    body: LinkInviteRequest,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> StatusResponse:
    """Relie le compte athlète courant au coach émetteur (sans créer de compte).

    Requiert une session authentifiée (login + 2FA déjà passés) : la preuve
    d'identité de l'athlète est ainsi établie. Refuse tout rôle ≠ athlète.
    """
    if user.get("role") != "athlete":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul un compte athlète peut être relié à un coach.",
        )
    if body.coach_code:
        coach = get_user_by_coach_invite_code(body.coach_code)
        if coach is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lien d'invitation invalide.",
            )
        link_coach_athlete(coach["id"], user["id"])
        log.info(
            "Athlète %s relié au coach %s (code)", user["public_id"][:8], coach["public_id"][:8]
        )
        return StatusResponse(status="linked")
    if body.invite_token:
        try:
            consume_invitation_for_link(body.invite_token, user["id"])
        except InvitationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        log.info("Athlète %s relié via invitation", user["public_id"][:8])
        return StatusResponse(status="linked")
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="invite_token ou coach_code requis.",
    )


# ---------------------------------------------------------------------------
# 2FA TOTP (authentifié) — enrôlement, vérification, désactivation
# ---------------------------------------------------------------------------


@router.post("/totp/enroll", response_model=TotpEnrollResponse)
def totp_enroll(
    body: TotpEnrollRequest | None = None,
    user: dict = Depends(get_current_user),  # noqa: B008
) -> TotpEnrollResponse:
    """Génère un secret TOTP non confirmé + QR à scanner. À confirmer via /totp/verify.

    Si une 2FA est déjà active, l'enrôlement (qui remplace le secret) exige une
    ré-authentification — mot de passe courant + code TOTP/code de secours — cf.
    ``_require_totp_reauth``. Le bootstrap reste exempté (break-glass).
    """
    if user.get("totp_enabled") and not user.get("is_bootstrap"):
        _require_totp_reauth(user, body)
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
