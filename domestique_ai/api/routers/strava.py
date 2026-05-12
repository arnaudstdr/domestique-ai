"""Endpoints de synchronisation Strava (en tâche de fond) et de maintenance."""

from __future__ import annotations

import datetime as dt
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from domestique_ai.api.deps import get_strava_client
from domestique_ai.api.schemas import SyncResult, SyncStatus
from domestique_ai.config import get_strava_credentials
from domestique_ai.ingestion.strava import (
    StravaAuthError,
    StravaClient,
    sync_activities,
)
from domestique_ai.ingestion.strava import (
    backfill_hr_zones as _backfill_hr_zones,
)
from domestique_ai.processing.analyzer import recalculate_training_loads

router = APIRouter(prefix="/api/strava", tags=["strava"])

# État de la dernière synchro lancée en tâche de fond.
_sync_state: dict[str, object] = {
    "status": "idle",
    "inserted": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_sync_lock = Lock()


def _run_sync() -> None:
    """Exécution du sync en background. Capture les exceptions."""
    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        with _sync_lock:
            _sync_state.update(
                status="error",
                error="STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents.",
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return
    try:
        client = StravaClient.from_tokens_file(client_id, client_secret)
        inserted = sync_activities(client)
    except StravaAuthError as exc:
        with _sync_lock:
            _sync_state.update(
                status="error",
                error=str(exc),
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return
    except Exception as exc:  # noqa: BLE001 — on remonte tout au front
        with _sync_lock:
            _sync_state.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return

    with _sync_lock:
        _sync_state.update(
            status="done",
            inserted=inserted,
            error=None,
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )


@router.post("/sync", response_model=SyncStatus)
def post_sync(background_tasks: BackgroundTasks) -> SyncStatus:
    """Déclenche un sync Strava en arrière-plan."""
    with _sync_lock:
        if _sync_state.get("status") == "syncing":
            return SyncStatus(**_sync_state)  # type: ignore[arg-type]
        _sync_state.update(
            status="syncing",
            inserted=None,
            error=None,
            started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            finished_at=None,
        )
    background_tasks.add_task(_run_sync)
    with _sync_lock:
        return SyncStatus(**_sync_state)  # type: ignore[arg-type]


@router.get("/sync-status", response_model=SyncStatus)
def get_sync_status() -> SyncStatus:
    """État courant de la dernière synchro lancée."""
    with _sync_lock:
        return SyncStatus(**_sync_state)  # type: ignore[arg-type]


@router.post("/recalculate", response_model=SyncResult)
def post_recalculate() -> SyncResult:
    """Recalcule la charge d'entraînement pour toute la base."""
    try:
        updated = recalculate_training_loads()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return SyncResult(status="done", updated=updated)


@router.post("/backfill-hr-zones", response_model=SyncResult)
def post_backfill_hr_zones(
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
) -> SyncResult:
    """Backfill des zones HR pour les activités déjà en base sans ventilation."""
    try:
        updated = _backfill_hr_zones(client)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except StravaAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return SyncResult(status="done", updated=updated)
