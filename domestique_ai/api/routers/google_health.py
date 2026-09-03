"""Router Google Health API : OAuth2, sync manuelle et statut."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    GoogleHealthStatusResponse,
    GoogleHealthSyncResponse,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_app_base_url, get_google_health_tokens_path
from domestique_ai.ingestion.google_health import (
    GoogleHealthClient,
    sync_google_health_morning_metrics,
)

log = get_logger("google_health_router")

router = APIRouter(prefix="/api/google-health", tags=["google-health"])


def _tokens_path(ctx: AthleteContext) -> Any:
    # Single-user bootstrap pour l'instant : tokens dans data/.google_health_tokens.json.
    # À terme, un fichier par athlète : ctx.tokens_path.with_suffix(".google_health.json").
    return get_google_health_tokens_path()


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
    request: Request,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> RedirectResponse:
    """Redirige l'utilisateur vers le consentement Google OAuth2."""
    client = _build_client(ctx)
    # Stocke un state minimal dans le token file (pas de session côté serveur).
    state = _generate_state(request, ctx)
    client.tokens["oauth_state"] = state
    client.save_tokens()
    auth_url = client.get_auth_url(state=state)
    return RedirectResponse(auth_url)


@router.get("/callback")
def get_google_health_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> RedirectResponse:
    """Callback OAuth2 Google Health : échange le code et redirige vers le front."""
    if error:
        log.warning("OAuth Google Health refusé : %s", error)
        return _redirect_front("google-health=denied")
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paramètre 'code' manquant.",
        )

    client = _build_client(ctx)
    stored_state = client.tokens.get("oauth_state")
    if stored_state and state != stored_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State OAuth invalide.",
        )

    try:
        client.exchange_code(code)
    except Exception:  # noqa: BLE001
        log.exception("Échec échange token Google Health.")
        return _redirect_front("google-health=error")

    client.tokens.pop("oauth_state", None)
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
    from domestique_ai.config import get_google_health_credentials

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


def _generate_state(request: Request, ctx: AthleteContext) -> str:
    import secrets

    return secrets.token_urlsafe(16)


def _record_sync(client: GoogleHealthClient) -> None:
    client.tokens["last_sync_at"] = dt.datetime.now(dt.UTC).isoformat()
    client.save_tokens()


def _redirect_front(query: str) -> RedirectResponse:
    base = get_app_base_url() or "/"
    separator = "?" if "?" not in base else "&"
    return RedirectResponse(f"{base}{separator}{query}")
