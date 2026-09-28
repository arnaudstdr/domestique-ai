"""Endpoints de synchronisation Garmin Connect (en tâche de fond).

Sync manuel ``POST /api/garmin/sync`` et auto-sync (scheduler) partagent le
même verrou par athlète — pas de chevauchement. L'authentification passe par le
cache token **de l'athlète** (``<athletes_root>/<public_id>/.garmin_tokens``) et
ses credentials stockés en DB plateforme ; la connexion se fait depuis l'UI
(email/mot de passe + MFA en 2 étapes — cf. ``POST /api/garmin/connect``).
"""

from __future__ import annotations

import datetime as dt
import time as _t
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    GarminConnectRequest,
    GarminConnectResponse,
    GarminMfaRequest,
    SyncStatus,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import garmin_token_dir_for
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

# ---------------------------------------------------------------------------
# Connexion MFA en 2 étapes.
#
# L'état MFA vit dans l'instance ``garminconnect`` (session SSO) et n'est pas
# sérialisable : on garde donc l'instance en mémoire process entre
# ``POST /connect`` (déclenche le MFA) et ``POST /connect/mfa`` (valide le code).
# ⚠️ Suppose un process uvicorn unique (cas actuel). Passer multi-worker
# nécessiterait un stockage partagé impossible à dériver du SDK.
# ---------------------------------------------------------------------------
_MFA_TTL_SEC = 300.0
_pending_mfa: dict[str, tuple[Any, Path, float]] = {}
_mfa_lock = Lock()


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
        client = get_ingest_client(
            token_dir=garmin_token_dir_for(ctx),
            email=ctx.garmin_email,
            password=ctx.garmin_password,
        )
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

    credentials = credentials_present(ctx.garmin_email, ctx.garmin_password)
    tokens = token_cache_present(garmin_token_dir_for(ctx))
    return {
        "credentials": credentials,
        "tokens": tokens,
        "connected": credentials and tokens,
        "email": ctx.garmin_email,
        "sync": _state_for(_public_key(ctx)),
    }


def _public_key(ctx: AthleteContext) -> str:
    """Clé d'état de sync — le chemin DB distingue les athlètes."""
    return str(ctx.db_path)


@router.post("/connect", response_model=GarminConnectResponse)
def post_connect(
    payload: GarminConnectRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> GarminConnectResponse:
    """Connecte le compte Garmin de l'athlète (email/mot de passe + MFA).

    Si Garmin réclame un code MFA, l'instance de login est mise en attente en
    mémoire et la réponse porte ``status="mfa_required"`` — l'UI enchaîne sur
    ``POST /api/garmin/connect/mfa``. Sinon, credentials + tokens sont persistés
    et ``status="connected"``.
    """
    from domestique_ai.export.garmin_connect import GarminPushError, start_login
    from domestique_ai.platform_db import set_user_garmin_credentials

    email = (payload.email or "").strip()
    password = payload.password or ""
    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email et mot de passe Garmin requis.",
        )

    token_dir = garmin_token_dir_for(ctx)
    try:
        result, client = start_login(email, password, token_dir)
    except GarminPushError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    set_user_garmin_credentials(_user_id_of(ctx), email, password)

    if result == "mfa_required":
        with _mfa_lock:
            _pending_mfa[_public_key(ctx)] = (client, token_dir, _t.monotonic())
        log.info("Garmin [%s] : MFA requis.", _public_key(ctx)[:8])
        return GarminConnectResponse(
            status="mfa_required",
            detail="Garmin demande un code MFA (email/SMS).",
        )

    log.info("Garmin [%s] : connexion réussie.", _public_key(ctx)[:8])
    return GarminConnectResponse(status="connected")


@router.post("/connect/mfa", response_model=GarminConnectResponse)
def post_connect_mfa(
    payload: GarminMfaRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> GarminConnectResponse:
    """Valide le code MFA d'un login Garmin en attente (cf. ``POST /connect``)."""
    from domestique_ai.export.garmin_connect import GarminPushError, finish_login

    key = _public_key(ctx)
    with _mfa_lock:
        pending = _pending_mfa.get(key)
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aucune connexion Garmin en attente. Relance la connexion.",
        )
    client, token_dir, started_at = pending
    if _t.monotonic() - started_at > _MFA_TTL_SEC:
        with _mfa_lock:
            _pending_mfa.pop(key, None)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session MFA expirée. Relance la connexion.",
        )

    try:
        finish_login(client, payload.code, token_dir)
    except GarminPushError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    with _mfa_lock:
        _pending_mfa.pop(key, None)
    log.info("Garmin [%s] : MFA validé, connexion établie.", key[:8])
    return GarminConnectResponse(status="connected")


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def post_disconnect(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
    """Déconnecte Garmin pour l'athlète : supprime tokens + credentials."""
    from domestique_ai.platform_db import clear_user_garmin_credentials

    key = _public_key(ctx)
    with _mfa_lock:
        _pending_mfa.pop(key, None)
    _remove_token_dir(garmin_token_dir_for(ctx))
    clear_user_garmin_credentials(_user_id_of(ctx))
    log.info("Garmin [%s] : déconnecté.", key[:8])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _remove_token_dir(token_dir: Path) -> None:
    """Supprime récursivement le dossier de tokens Garmin (best-effort)."""
    import shutil

    try:
        if token_dir.exists():
            shutil.rmtree(token_dir, ignore_errors=True)
    except OSError:  # noqa: BLE001 — best-effort
        log.warning("Garmin : suppression du token dir %s échouée.", token_dir, exc_info=True)


def _user_id_of(ctx: AthleteContext) -> int:
    """Id plateforme de l'athlète du contexte (résolu par public_id)."""
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, get_user_by_public_id

    if not ctx.public_id:
        return int(get_or_create_bootstrap_coach()["id"])
    user = get_user_by_public_id(ctx.public_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Athlète introuvable.",
        )
    return int(user["id"])


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
