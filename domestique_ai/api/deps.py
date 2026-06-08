"""Dépendances FastAPI partagées (client Strava, utilisateur courant, etc.)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import AthleteContext, context_for_athlete
from domestique_ai.config import get_api_token, get_strava_credentials
from domestique_ai.ingestion.strava import StravaAuthError, StravaClient

log = get_logger("deps")


def get_current_user(request: Request) -> dict[str, Any]:
    """Utilisateur courant, résolu par ``BearerAuthMiddleware`` dans ``request.state``.

    En mode auth-off (pas de ``DOMESTIQUE_AI_API_TOKEN``), retombe sur le coach
    propriétaire (bootstrap) — cohérent avec le mono-utilisateur historique.
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    if get_api_token() is None:
        from domestique_ai.platform_db import get_or_create_bootstrap_coach
        return get_or_create_bootstrap_coach()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
    )


def require_coach(request: Request) -> dict[str, Any]:
    """Exige un utilisateur de rôle coach (403 sinon)."""
    user = get_current_user(request)
    if user.get("role") != "coach":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Coach role required"
        )
    return user


def get_athlete_context(request: Request) -> AthleteContext:
    """Contexte de données de l'utilisateur courant (bootstrap → données legacy)."""
    return context_for_athlete(get_current_user(request))


def get_strava_client(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> StravaClient:
    """Construit un `StravaClient` à partir des tokens de l'athlète courant.

    Lève une 503 si les credentials d'app ne sont pas configurés ou si l'athlète
    n'a pas (encore) connecté son Strava (tokens absents — avant l'onboarding 1c).
    """
    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        log.warning("Strava client demandé mais credentials absents.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents de l'environnement."
            ),
        )
    try:
        return StravaClient.from_tokens_file(client_id, client_secret, ctx=ctx)
    except StravaAuthError as exc:
        log.warning("Strava client : auth/token KO : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
