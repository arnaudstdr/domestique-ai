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
from domestique_ai.healthcheck import ping_healthcheck

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None

_DEFAULT_INTERVAL_MIN = 30
_DEFAULT_FIRST_RUN_DELAY_MIN = 2
_DEFAULT_HEALTHCHECK_INTERVAL_MIN = 5


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


def _sync_targets() -> list[tuple[dict, object]]:
    """Athlètes à synchroniser : le propriétaire + ceux ayant des tokens Strava.

    Le propriétaire (bootstrap) est toujours inclus (ses tokens legacy) ; les
    autres athlètes ne sont retenus qu'une fois leur Strava connecté (fichier
    tokens présent — après l'onboarding web 1c).
    """
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, list_users

    users = list_users()
    if not any(u.get("is_bootstrap") for u in users):
        get_or_create_bootstrap_coach()
        users = list_users()

    targets: list[tuple[dict, object]] = []
    for user in users:
        ctx = context_for_athlete(user)
        if ctx.tokens_path.exists():
            targets.append((user, ctx))
    return targets


def _auto_sync_job() -> None:
    """Job appelé à chaque tick — sync chaque athlète ayant des tokens."""
    try:
        targets = _sync_targets()
    except Exception:  # noqa: BLE001 — un job APScheduler ne doit jamais lever
        log.exception("Auto-sync Strava : énumération des athlètes échouée.")
        return
    for user, ctx in targets:
        key = user["public_id"]
        try:
            if not trigger_sync_blocking(ctx, key, user=user):
                log.info("Auto-sync [%s] : skip (déjà en cours).", key[:8])
        except Exception:  # noqa: BLE001 — un athlète KO n'arrête pas les autres
            log.exception("Auto-sync [%s] : exception non gérée.", key[:8])


def _healthcheck_interval_minutes() -> int:
    return _read_positive_int(
        "HEALTHCHECKS_PING_INTERVAL_MIN", _DEFAULT_HEALTHCHECK_INTERVAL_MIN
    )


def _healthcheck_ping_job() -> None:
    """Push un ping vers Healthchecks.io — exceptions toujours capturées."""
    try:
        ping_healthcheck()
    except Exception:  # noqa: BLE001 — best-effort, jamais fatal
        log.exception("Healthcheck ping : exception non gérée.")


def start_scheduler() -> None:
    """Démarre le scheduler s'il n'est pas déjà en cours.

    Enregistre deux jobs indépendants :
    - ``strava_auto_sync`` : sync Strava périodique (si activé)
    - ``healthcheck_ping`` : ping Healthchecks.io périodique (si URL configurée)

    No-op global si déjà démarré. Chaque job est ajouté seulement si sa
    configuration est valide — on peut donc avoir l'un sans l'autre.
    """
    global _scheduler
    if _scheduler is not None:
        return

    interval = _auto_sync_interval_minutes()
    hc_interval = _healthcheck_interval_minutes()
    hc_url_configured = os.getenv("HEALTHCHECKS_PING_URL", "").strip() != ""

    sync_enabled = interval > 0
    hc_enabled = hc_interval > 0 and hc_url_configured

    if not sync_enabled and not hc_enabled:
        if interval == 0:
            log.info(
                "Auto-sync Strava désactivé (DOMESTIQUE_AI_AUTO_SYNC_MINUTES=0)."
            )
        if not hc_url_configured:
            log.info("Healthchecks ping désactivé (HEALTHCHECKS_PING_URL absent).")
        return

    scheduler = BackgroundScheduler(timezone="UTC")

    if sync_enabled:
        delay = _first_run_delay_minutes()
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
        log.info(
            "Scheduler : sync Strava toutes les %d min (1er run dans %d min).",
            interval,
            delay,
        )
    else:
        log.info(
            "Auto-sync Strava désactivé (DOMESTIQUE_AI_AUTO_SYNC_MINUTES=0)."
        )

    if hc_enabled:
        # 1er ping immédiat (au démarrage) pour confirmer à Healthchecks.io
        # que l'app vient de se lancer — utile pour les notifs de redémarrage.
        scheduler.add_job(
            _healthcheck_ping_job,
            "interval",
            minutes=hc_interval,
            id="healthcheck_ping",
            coalesce=True,
            max_instances=1,
            next_run_time=dt.datetime.now(dt.timezone.utc),
        )
        log.info(
            "Scheduler : ping Healthchecks.io toutes les %d min.", hc_interval
        )
    elif not hc_url_configured:
        log.info("Healthchecks ping désactivé (HEALTHCHECKS_PING_URL absent).")

    scheduler.start()
    _scheduler = scheduler


def stop_scheduler() -> None:
    """Arrête le scheduler proprement (no-op si pas démarré)."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Scheduler arrêté.")
