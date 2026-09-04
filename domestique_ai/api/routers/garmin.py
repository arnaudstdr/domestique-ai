"""Endpoints de synchronisation Garmin Connect (en tâche de fond).

Sync manuel ``POST /api/garmin/sync`` et auto-sync (scheduler) partagent le
même verrou par athlète — pas de chevauchement. L'authentification passe par
le cache token Garmin partagé avec le module d'export (seed interactif MFA :
``python -m domestique_ai.export.garmin_connect``).
"""

from __future__ import annotations

import datetime as dt
import time as _t
from threading import Lock
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import SyncStatus
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.garmin import (
    GarminIngestError,
    get_ingest_client,
    sync_activities_garmin,
)

router = APIRouter(prefix="/api/garmin", tags=["garmin"])
log = get_logger("garmin")

# État de la dernière synchro, indexé par athlète (public_id).
_sync_state: dict[str, dict[str, Any]] = {}
_sync_lock = Lock()


def _idle_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "inserted": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


def _state_for(key: str) -> dict[str, Any]:
    with _sync_lock:
        return dict(_sync_state.get(key) or _idle_state())


def _set_state(key: str, **fields: Any) -> None:
    with _sync_lock:
        base = _sync_state.get(key) or _idle_state()
        base.update(fields)
        _sync_state[key] = base


def _claim_sync(key: str) -> bool:
    with _sync_lock:
        if (_sync_state.get(key) or {}).get("status") == "syncing":
            return False
        _sync_state[key] = {
            "status": "syncing",
            "inserted": None,
            "error": None,
            "started_at": dt.datetime.now(dt.UTC).isoformat(),
            "finished_at": None,
        }
    return True


def trigger_sync_blocking(ctx: AthleteContext, key: str) -> bool:
    """Réserve + exécute un sync synchrone (utilisé par l'auto-sync scheduler)."""
    if not _claim_sync(key):
        return False
    _run_sync(ctx, key)
    return True


def _run_sync(ctx: AthleteContext, key: str) -> None:
    start = _t.perf_counter()
    log.info("Sync Garmin [%s] : démarrage…", key[:8])
    try:
        client = get_ingest_client()
        inserted = sync_activities_garmin(client, ctx=ctx)
    except GarminIngestError as exc:
        log.error("Sync Garmin [%s] : erreur : %s", key[:8], exc)
        _set_state(
            key,
            status="error",
            error=str(exc),
            finished_at=dt.datetime.now(dt.UTC).isoformat(),
        )
        return
    except Exception as exc:  # noqa: BLE001 — on remonte tout au front
        log.exception("Sync Garmin [%s] : exception non gérée", key[:8])
        _set_state(
            key,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=dt.datetime.now(dt.UTC).isoformat(),
        )
        return

    duration = _t.perf_counter() - start
    log.info(
        "Sync Garmin [%s] : terminé en %.1fs — %d nouvelle(s) activité(s).",
        key[:8],
        duration,
        inserted,
    )
    _set_state(
        key,
        status="done",
        inserted=inserted,
        error=None,
        finished_at=dt.datetime.now(dt.UTC).isoformat(),
    )

    if inserted > 0:
        try:
            from domestique_ai.notifications import notify_sync_completed

            notify_sync_completed(inserted)
        except Exception:  # noqa: BLE001 — log mais ne propage pas
            log.exception("Sync Garmin : notif push échouée")


@router.get("/status")
def get_status(ctx: AthleteContext = Depends(get_athlete_context)) -> dict[str, Any]:  # noqa: B008
    """Statut de la connexion Garmin pour l'athlète courant."""
    from domestique_ai.export.garmin_connect import credentials_present, token_cache_present

    return {
        "credentials": credentials_present(),
        "tokens": token_cache_present(),
        "connected": credentials_present() and token_cache_present(),
        "sync": _state_for(_public_key(ctx)),
    }


def _public_key(ctx: AthleteContext) -> str:
    """Clé d'état de sync — le chemin DB distingue les athlètes."""
    return str(ctx.db_path)


@router.post("/sync", response_model=SyncStatus)
def post_sync(
    background_tasks: BackgroundTasks,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncStatus:
    """Lance un sync Garmin en tâche de fond et retourne l'état courant."""
    key = _public_key(ctx)
    if not _claim_sync(key):
        return SyncStatus(**_state_for(key))
    background_tasks.add_task(_run_sync, ctx, key)
    return SyncStatus(**_state_for(key))


@router.get("/sync-status", response_model=SyncStatus)
def get_sync_status(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncStatus:
    """État de la dernière synchro Garmin."""
    return SyncStatus(**_state_for(_public_key(ctx)))
