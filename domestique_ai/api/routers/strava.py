"""Endpoints de synchronisation Strava (en tâche de fond) et de maintenance."""

from __future__ import annotations

import datetime as dt
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from domestique_ai.api.deps import get_strava_client
from domestique_ai.api.logging import get_logger
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
from domestique_ai.ingestion.strava import (
    backfill_temperature as _backfill_temperature,
)
from domestique_ai.processing.analyzer import recalculate_training_loads

router = APIRouter(prefix="/api/strava", tags=["strava"])
log = get_logger("strava")

# État de la dernière synchro lancée en tâche de fond.
_sync_state: dict[str, object] = {
    "status": "idle",
    "inserted": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_sync_lock = Lock()


def _claim_sync() -> bool:
    """Tente de réserver le slot de sync. Retourne ``True`` si réussi.

    Sync manuel (endpoint ``POST /sync``) et auto-sync (scheduler) passent
    tous les deux par ce point pour éviter le chevauchement. Si une sync est
    déjà en cours, l'appelant doit abandonner ou attendre le tick suivant.
    """
    with _sync_lock:
        if _sync_state.get("status") == "syncing":
            return False
        _sync_state.update(
            status="syncing",
            inserted=None,
            error=None,
            started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            finished_at=None,
        )
    return True


def trigger_sync_blocking() -> bool:
    """Réserve + exécute un sync de manière synchrone dans le thread courant.

    Utilisé par l'auto-sync (scheduler). Retourne ``True`` si l'exécution a
    eu lieu, ``False`` si une autre sync était déjà en cours.
    """
    if not _claim_sync():
        return False
    _run_sync()
    return True


def _run_sync() -> None:
    """Exécution du sync en background. Capture les exceptions."""
    import time as _t  # local — évite collision avec time.time global
    start = _t.perf_counter()
    log.info("Sync Strava : démarrage…")

    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        log.error("Sync Strava : credentials absents (STRAVA_CLIENT_ID/SECRET).")
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
        log.error("Sync Strava : erreur d'authentification : %s", exc)
        with _sync_lock:
            _sync_state.update(
                status="error",
                error=str(exc),
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return
    except Exception as exc:  # noqa: BLE001 — on remonte tout au front
        log.exception("Sync Strava : exception non gérée")
        with _sync_lock:
            _sync_state.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return

    duration = _t.perf_counter() - start
    log.info("Sync Strava : terminé en %.1fs — %d nouvelle(s) activité(s).",
             duration, inserted)
    with _sync_lock:
        _sync_state.update(
            status="done",
            inserted=inserted,
            error=None,
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )

    # Notif push best-effort — n'altère ni le sync ni l'état exposé à l'API.
    # Import local pour éviter un cycle (notifications utilise api.logging).
    if inserted > 0:
        from domestique_ai.notifications import notify_sync_completed

        try:
            notify_sync_completed(inserted)
        except Exception:  # noqa: BLE001 — log mais ne propage pas
            log.exception("Sync Strava : notif push échouée")


@router.post("/sync", response_model=SyncStatus)
def post_sync(background_tasks: BackgroundTasks) -> SyncStatus:
    """Déclenche un sync Strava en arrière-plan."""
    if _claim_sync():
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
    log.info("Recalcul charge : démarrage…")
    try:
        updated = recalculate_training_loads()
    except Exception as exc:  # noqa: BLE001
        log.exception("Recalcul charge : exception")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    log.info("Recalcul charge : %d ligne(s) mises à jour.", updated)
    return SyncResult(status="done", updated=updated)


@router.post("/backfill-hr-zones", response_model=SyncResult)
def post_backfill_hr_zones(
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
) -> SyncResult:
    """Backfill des zones HR pour les activités déjà en base sans ventilation."""
    log.info("Backfill zones HR : démarrage…")
    try:
        updated = _backfill_hr_zones(client)
    except RuntimeError as exc:
        log.warning("Backfill zones HR : configuration invalide : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except StravaAuthError as exc:
        log.warning("Backfill zones HR : auth Strava : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    log.info("Backfill zones HR : %d activité(s) ventilée(s).", updated)
    return SyncResult(status="done", updated=updated)


@router.post("/backfill-temperature", response_model=SyncResult)
def post_backfill_temperature(
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
) -> SyncResult:
    """Backfill température (min/avg/max) pour les activités sans donnée temp.

    1 appel API par activité — attention au rate limit Strava (100 req / 15 min).
    Idempotent : ne touche que les lignes ``avg_temp IS NULL``.
    """
    log.info("Backfill température : démarrage…")
    try:
        updated = _backfill_temperature(client)
    except StravaAuthError as exc:
        log.warning("Backfill température : auth Strava : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    log.info("Backfill température : %d activité(s) enrichie(s).", updated)
    return SyncResult(status="done", updated=updated)
