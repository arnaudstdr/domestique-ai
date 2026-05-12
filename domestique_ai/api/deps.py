"""Dépendances FastAPI partagées (client Strava, paths, etc.)."""

from __future__ import annotations

from fastapi import HTTPException, status

from domestique_ai.config import get_strava_credentials
from domestique_ai.ingestion.strava import StravaAuthError, StravaClient


def get_strava_client() -> StravaClient:
    """Construit un `StravaClient` à partir du token local.

    Lève une 503 si les credentials ne sont pas configurés ou si le flow
    OAuth n'a pas encore été exécuté.
    """
    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents de l'environnement."
            ),
        )
    try:
        return StravaClient.from_tokens_file(client_id, client_secret)
    except StravaAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
