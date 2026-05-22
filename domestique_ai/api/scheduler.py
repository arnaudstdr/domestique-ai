"""Auto-sync Strava périodique via APScheduler.

Le scheduler tourne dans le process FastAPI (BackgroundScheduler — thread
pool dédié, pas l'event loop asyncio). Il appelle ``trigger_sync_blocking``
qui partage son verrou avec le sync manuel ``POST /api/strava/sync`` : pas
de risque de chevauchement.

Configuration :
- ``DOMESTIQUE_AI_AUTO_SYNC_MINUTES`` : période en minutes (défaut 30).
  ``0`` désactive complètement l'auto-sync.
- ``DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN`` : délai avant le 1er run
  après le démarrage (défaut 2 min — laisse l'API se stabiliser).
"""

from __future__ import annotations

import datetime as dt
import os

from apscheduler.schedulers.background import BackgroundScheduler

from domestique_ai.api.logging import get_logger
from domestique_ai.api.routers.strava import trigger_sync_blocking

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None

_DEFAULT_INTERVAL_MIN = 30
_DEFAULT_FIRST_RUN_DELAY_MIN = 2


def _read_positive_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("%s invalide (%r), fallback %d.", env_name, raw, default)
        return default
    return max(0, v)


def _auto_sync_interval_minutes() -> int:
    return _read_positive_int(
        "DOMESTIQUE_AI_AUTO_SYNC_MINUTES", _DEFAULT_INTERVAL_MIN
    )


def _first_run_delay_minutes() -> int:
    return _read_positive_int(
        "DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN",
        _DEFAULT_FIRST_RUN_DELAY_MIN,
    )


def _auto_sync_job() -> None:
    """Job appelé à chaque tick — skip silencieux si occupé."""
    try:
        triggered = trigger_sync_blocking()
    except Exception:  # noqa: BLE001 — un job APScheduler ne doit jamais lever
        log.exception("Auto-sync Strava : exception non gérée.")
        return
    if not triggered:
        log.info("Auto-sync Strava : skip (sync déjà en cours).")


def start_scheduler() -> None:
    """Démarre le scheduler s'il n'est pas déjà en cours.

    No-op si déjà démarré ou si l'auto-sync est désactivé.
    """
    global _scheduler
    if _scheduler is not None:
        return
    interval = _auto_sync_interval_minutes()
    if interval == 0:
        log.info(
            "Auto-sync Strava désactivé (DOMESTIQUE_AI_AUTO_SYNC_MINUTES=0)."
        )
        return
    delay = _first_run_delay_minutes()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _auto_sync_job,
        "interval",
        minutes=interval,
        id="strava_auto_sync",
        coalesce=True,
        max_instances=1,
        next_run_time=dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(minutes=delay),
    )
    scheduler.start()
    _scheduler = scheduler
    log.info(
        "Scheduler démarré : sync Strava toutes les %d min (1er run dans %d min).",
        interval,
        delay,
    )


def stop_scheduler() -> None:
    """Arrête le scheduler proprement (no-op si pas démarré)."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Scheduler arrêté.")
