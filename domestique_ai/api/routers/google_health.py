"""Router Google Health API : OAuth2, sync manuelle et statut."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, Response

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    GoogleHealthStatusResponse,
    GoogleHealthSyncResponse,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import (
    get_app_base_url,
    get_google_health_credentials,
    get_session_secret,
    google_health_tokens_path_for,
)
from domestique_ai.ingestion.google_health import (
    GoogleHealthClient,
    sync_google_health_morning_metrics,
)

log = get_logger("google_health_router")

router = APIRouter(prefix="/api/google-health", tags=["google-health"])


def _tokens_path(ctx: AthleteContext) -> Any:
    """Fichier de tokens Google Health de l'athlète (chemin per-athlète)."""
    return google_health_tokens_path_for(ctx)


def _client_or_404(ctx: AthleteContext) -> GoogleHealthClient:
    client = GoogleHealthClient.from_tokens_file(_tokens_path(ctx))
    if client is None or not client.is_authenticated():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Health n'est pas connecté.",
        )
    return client


@router.get("/status", response_model=GoogleHealthStatusResponse)
def get_google_health_status(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> GoogleHealthStatusResponse:
    """Indique si Google Health est configuré et authentifié."""
    client = GoogleHealthClient.from_tokens_file(_tokens_path(ctx))
    configured = client is not None or _has_credentials()
    authenticated = client is not None and client.is_authenticated()
    last_sync = client.tokens.get("last_sync_at") if client else None
    return GoogleHealthStatusResponse(
        configured=configured,
        authenticated=authenticated,
        last_sync_at=last_sync,
    )


def _has_credentials() -> bool:
    from domestique_ai.config import get_google_health_credentials

    client_id, client_secret, _ = get_google_health_credentials()
    return bool(client_id and client_secret)


@router.get("/auth")
def get_google_health_auth(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> dict[str, str]:
    """Retourne l'URL de consentement Google OAuth2 pour redirection front.

    Le ``state`` est **auto-porteur** (public_id signé HMAC) : le callback étant
    appelé par Google sans Bearer, il doit pouvoir retrouver l'athlète cible sans
    session serveur ni fichier partagé.
    """
    client = _build_client(ctx)
    state = _sign_state(ctx.public_id)
    return {"auth_url": client.get_auth_url(state=state)}


@router.get("/callback")
def get_google_health_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Callback OAuth2 Google Health : échange le code et redirige vers le front.

    Appelé par Google (redirection navigateur), donc exempté du Bearer auth. Le
    ``state`` signé identifie l'athlète destinataire des tokens — chacun garde son
    propre fichier (``<athletes_root>/<public_id>/.google_health_tokens.json``).
    """
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, get_user_by_public_id

    if error:
        log.warning("OAuth Google Health refusé : %s", error)
        return _redirect_front("google-health=denied")
    if not code:
        log.warning("Callback Google Health : paramètre 'code' manquant.")
        return _redirect_front("google-health=error")

    public_id = _verify_state(state)
    if public_id is None:
        log.warning("Callback Google Health : state OAuth invalide.")
        return _redirect_front("google-health=error")

    user = get_user_by_public_id(public_id) if public_id else get_or_create_bootstrap_coach()
    if user is None:
        log.warning("Callback Google Health : athlète %s introuvable.", public_id[:8])
        return _redirect_front("google-health=error")

    ctx = context_for_athlete(user)
    client = _build_client(ctx)

    try:
        client.exchange_code(code)
    except Exception:  # noqa: BLE001
        log.exception("Échec échange token Google Health.")
        return _redirect_front("google-health=error")

    client.save_tokens()
    return _redirect_front("google-health=connected")


@router.post("/sync", response_model=GoogleHealthSyncResponse)
def post_google_health_sync(
    days: int = Query(default=7, ge=1, le=90),
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> GoogleHealthSyncResponse:
    """Déclenche une sync manuelle des métriques matinales depuis Google Health."""
    client = _client_or_404(ctx)
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days - 1)

    try:
        result = sync_google_health_morning_metrics(
            client,
            start_date=start_date,
            end_date=end_date,
            db_path=ctx.db_path,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Sync Google Health échouée.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sync Google Health échouée : {exc}",
        ) from exc

    _record_sync(client)
    message = (
        f"{len(result['synced_dates'])} jour(s) synchronisé(s), "
        f"{len(result['skipped_dates'])} sans donnée."
    )
    return GoogleHealthSyncResponse(
        success=True,
        synced_dates=result["synced_dates"],
        skipped_dates=result["skipped_dates"],
        message=message,
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def post_google_health_disconnect(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
    """Révoque les tokens Google Health et supprime le fichier local."""
    client = GoogleHealthClient.from_tokens_file(_tokens_path(ctx))
    if client and client.is_authenticated():
        client.revoke_tokens()
    else:
        path = _tokens_path(ctx)
        if path.exists():
            path.unlink()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _build_client(ctx: AthleteContext) -> GoogleHealthClient:
    client_id, client_secret, redirect_uri = get_google_health_credentials()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Health n'est pas configuré (credentials manquants).",
        )
    path = _tokens_path(ctx)
    tokens: dict[str, Any] = {}
    if path.exists():
        import json

        try:
            tokens = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tokens = {}
    return GoogleHealthClient(
        tokens=tokens,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        tokens_path=path,
    )


def _sign_state(public_id: str) -> str:
    """Encode ``public_id`` + une signature HMAC dans un ``state`` OAuth signé.

    Format ``<b64url(public_id)>.<b64url(hmac)>``. Le callback peut ainsi
    retrouver l'athlète destinataire sans stockage serveur (pas de session, pas
    de fichier partagé).
    """
    import base64
    import hashlib
    import hmac

    raw = public_id.encode("utf-8")
    sig = hmac.new(get_session_secret(), b"google-health-state:" + raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    )


def _verify_state(state: str | None) -> str | None:
    """Vérifie un ``state`` signé et retourne le ``public_id`` (``""`` bootstrap).

    ``None`` si absent, mal formé ou signature invalide.
    """
    import base64
    import hashlib
    import hmac

    if not state or "." not in state:
        return None
    encoded, _, signature = state.partition(".")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(
        get_session_secret(), b"google-health-state:" + raw, hashlib.sha256
    ).digest()
    try:
        padding_sig = "=" * (-len(signature) % 4)
        provided = base64.urlsafe_b64decode(signature + padding_sig)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _record_sync(client: GoogleHealthClient) -> None:
    client.tokens["last_sync_at"] = dt.datetime.now(dt.UTC).isoformat()
    client.save_tokens()


def _redirect_front(query: str) -> RedirectResponse:
    base = get_app_base_url() or "/"
    separator = "?" if "?" not in base else "&"
    return RedirectResponse(f"{base}{separator}{query}")
