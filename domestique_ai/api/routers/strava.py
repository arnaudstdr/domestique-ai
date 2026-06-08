"""Endpoints de synchronisation Strava (en tâche de fond) et de maintenance."""

from __future__ import annotations

import datetime as dt
from threading import Lock
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from domestique_ai.api.deps import (
    get_athlete_context,
    get_current_user,
    get_strava_client,
)
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import SyncResult, SyncStatus
from domestique_ai.athlete_context import AthleteContext, context_for_athlete
from domestique_ai.config import get_app_base_url, get_strava_credentials
from domestique_ai.ingestion.strava import (
    StravaAuthError,
    StravaClient,
    sync_activities,
)
from domestique_ai.ingestion.strava import (
    backfill_hr_zones as _backfill_hr_zones,
)
from domestique_ai.ingestion.strava import (
    backfill_polylines as _backfill_polylines,
)
from domestique_ai.ingestion.strava import (
    backfill_temperature as _backfill_temperature,
)
from domestique_ai.platform_db import consume_oauth_state, create_oauth_state
from domestique_ai.processing.analyzer import recalculate_training_loads

router = APIRouter(prefix="/api/strava", tags=["strava"])
log = get_logger("strava")

# Durée de vie d'un state OAuth (anti-CSRF) : large pour laisser l'athlète
# s'authentifier sur Strava, mais borné.
_OAUTH_STATE_TTL_MIN = 15

# État de la dernière synchro lancée, INDEXÉ PAR athlète (public_id).
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
    """Copie de l'état de sync de l'athlète ``key`` (idle si jamais syncé)."""
    with _sync_lock:
        return dict(_sync_state.get(key) or _idle_state())


def _set_state(key: str, **fields: Any) -> None:
    with _sync_lock:
        base = _sync_state.get(key) or _idle_state()
        base.update(fields)
        _sync_state[key] = base


def _claim_sync(key: str) -> bool:
    """Réserve le slot de sync de l'athlète ``key``. ``True`` si réussi.

    Sync manuel (``POST /sync``) et auto-sync (scheduler) passent par ce point.
    Le verrou est global mais l'état est par athlète : deux athlètes peuvent
    syncer en parallèle, mais pas deux syncs du même athlète.
    """
    with _sync_lock:
        if (_sync_state.get(key) or {}).get("status") == "syncing":
            return False
        _sync_state[key] = {
            "status": "syncing",
            "inserted": None,
            "error": None,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "finished_at": None,
        }
    return True


def trigger_sync_blocking(ctx: AthleteContext, key: str, *,
                          user: dict | None = None) -> bool:
    """Réserve + exécute un sync synchrone pour l'athlète ``key``/``ctx``.

    Utilisé par l'auto-sync (scheduler). Retourne ``True`` si l'exécution a eu
    lieu, ``False`` si une sync du même athlète était déjà en cours.
    """
    if not _claim_sync(key):
        return False
    _run_sync(ctx, key, user=user)
    return True


def _run_sync(ctx: AthleteContext, key: str, *, user: dict | None = None) -> None:
    """Exécution du sync en background pour un athlète. Capture les exceptions."""
    import time as _t  # local — évite collision avec time.time global
    start = _t.perf_counter()
    log.info("Sync Strava [%s] : démarrage…", key[:8])

    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        log.error("Sync Strava : credentials absents (STRAVA_CLIENT_ID/SECRET).")
        _set_state(
            key,
            status="error",
            error="STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents.",
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        return
    try:
        client = StravaClient.from_tokens_file(client_id, client_secret, ctx=ctx)
        inserted = sync_activities(client, ctx=ctx)
    except StravaAuthError as exc:
        log.error("Sync Strava [%s] : erreur d'authentification : %s", key[:8], exc)
        _set_state(
            key, status="error", error=str(exc),
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        return
    except Exception as exc:  # noqa: BLE001 — on remonte tout au front
        log.exception("Sync Strava [%s] : exception non gérée", key[:8])
        _set_state(
            key, status="error", error=f"{type(exc).__name__}: {exc}",
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        return

    duration = _t.perf_counter() - start
    log.info("Sync Strava [%s] : terminé en %.1fs — %d nouvelle(s) activité(s).",
             key[:8], duration, inserted)
    _set_state(
        key, status="done", inserted=inserted, error=None,
        finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )

    # Notif push best-effort — n'altère ni le sync ni l'état exposé à l'API.
    # Import local pour éviter un cycle (notifications utilise api.logging).
    if inserted > 0:
        from domestique_ai.notifications import notify_sync_completed

        try:
            notify_sync_completed(inserted, user=user)
        except Exception:  # noqa: BLE001 — log mais ne propage pas
            log.exception("Sync Strava : notif push échouée")


@router.post("/sync", response_model=SyncStatus)
def post_sync(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),  # noqa: B008
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncStatus:
    """Déclenche un sync Strava de l'athlète courant en arrière-plan."""
    key = user["public_id"]
    if _claim_sync(key):
        background_tasks.add_task(_run_sync, ctx, key, user=user)
    return SyncStatus(**_state_for(key))


@router.get("/sync-status", response_model=SyncStatus)
def get_sync_status(
    user: dict = Depends(get_current_user),  # noqa: B008
) -> SyncStatus:
    """État courant de la dernière synchro de l'athlète courant."""
    return SyncStatus(**_state_for(user["public_id"]))


# ---------------------------------------------------------------------------
# Onboarding OAuth Strava par athlète (palier 1c)
# ---------------------------------------------------------------------------

@router.get("/connection")
def get_connection(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> dict[str, bool]:
    """Indique si l'athlète courant a connecté son Strava (fichier tokens présent)."""
    return {"connected": ctx.tokens_path.exists()}


@router.get("/authorize")
def get_authorize(
    user: dict = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    """Crée un state OAuth lié à l'athlète et renvoie l'URL d'autorisation Strava."""
    client_id, _client_secret, redirect_uri = get_strava_credentials()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STRAVA_CLIENT_ID absent de l'environnement.",
        )
    expires_at = (
        dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(minutes=_OAUTH_STATE_TTL_MIN)
    ).isoformat()
    _state_row, state = create_oauth_state(user["id"], expires_at=expires_at)
    base_url = StravaClient.get_authorization_url(client_id, redirect_uri)
    sep = "&" if "?" in base_url else "?"
    return {"authorize_url": f"{base_url}{sep}state={state}"}


@router.get("/callback")
def get_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Callback OAuth Strava (navigation navigateur, exempté du Bearer).

    Valide le ``state`` (→ athlète), échange le ``code`` contre des tokens,
    les écrit dans l'espace de l'athlète, lance sa 1re sync, puis redirige le
    navigateur vers le frontend. Toute erreur redirige vers ``?strava=error``
    (on ne renvoie pas d'exception HTTP à un client navigateur).
    """
    base = get_app_base_url()
    fail = RedirectResponse(url=f"{base}/?strava=error", status_code=302)

    if error or not (code and state):
        log.warning("Callback Strava : refus ou paramètres manquants (error=%s).", error)
        return fail

    user = consume_oauth_state(state)
    if user is None:
        log.warning("Callback Strava : state invalide/expiré/déjà utilisé.")
        return fail

    client_id, client_secret, redirect_uri = get_strava_credentials()
    if not (client_id and client_secret):
        log.error("Callback Strava : credentials d'app absents.")
        return fail

    try:
        tokens = StravaClient.exchange_code_for_token(
            client_id, client_secret, code, redirect_uri
        )
        client = StravaClient(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=tokens.get("expires_at", 0),
            client_id=client_id,
            client_secret=client_secret,
        )
        ctx = context_for_athlete(user)
        client.save_tokens(ctx=ctx)
    except Exception:  # noqa: BLE001 — échec d'échange → retour propre au front
        log.exception("Callback Strava : échange du code échoué (user=%s).",
                      user["public_id"][:8])
        return fail

    # 1re sync en arrière-plan (best-effort).
    key = user["public_id"]
    if _claim_sync(key):
        background_tasks.add_task(_run_sync, ctx, key, user=user)

    log.info("Strava connecté pour l'athlète %s — 1re sync lancée.", key[:8])
    return RedirectResponse(url=f"{base}/?strava=connected", status_code=302)


@router.post("/recalculate", response_model=SyncResult)
def post_recalculate(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncResult:
    """Recalcule la charge d'entraînement de l'athlète courant."""
    log.info("Recalcul charge : démarrage…")
    try:
        updated = recalculate_training_loads(ctx=ctx)
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
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncResult:
    """Backfill des zones HR pour les activités déjà en base sans ventilation."""
    log.info("Backfill zones HR : démarrage…")
    try:
        updated = _backfill_hr_zones(client, ctx=ctx)
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
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncResult:
    """Backfill température (min/avg/max) pour les activités sans donnée temp.

    1 appel API par activité — attention au rate limit Strava (100 req / 15 min).
    Idempotent : ne touche que les lignes ``avg_temp IS NULL``.
    """
    log.info("Backfill température : démarrage…")
    try:
        updated = _backfill_temperature(client, ctx=ctx)
    except StravaAuthError as exc:
        log.warning("Backfill température : auth Strava : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    log.info("Backfill température : %d activité(s) enrichie(s).", updated)
    return SyncResult(status="done", updated=updated)


@router.post("/backfill-polylines", response_model=SyncResult)
def post_backfill_polylines(
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncResult:
    """Backfill ``map_polyline`` pour les activités sans tracé persisté.

    Très peu coûteux : le ``summary_polyline`` est inclus dans le listing
    Strava (200 activités / requête), pas besoin d'un appel par activité.
    Idempotent : ne touche que les lignes ``map_polyline IS NULL``.
    """
    log.info("Backfill tracés : démarrage…")
    try:
        updated = _backfill_polylines(client, ctx=ctx)
    except StravaAuthError as exc:
        log.warning("Backfill tracés : auth Strava : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    log.info("Backfill tracés : %d activité(s) enrichie(s).", updated)
    return SyncResult(status="done", updated=updated)
