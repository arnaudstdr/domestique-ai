"""Auto-sync périodique (Garmin, Google Health, heartbeat) via APScheduler.

Le scheduler tourne dans le process FastAPI (BackgroundScheduler — thread
pool dédié, pas l'event loop asyncio). Chaque job partage le verrou de son
router avec le sync manuel correspondant : pas de risque de chevauchement.

Configuration :
- ``DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES`` : période Garmin en minutes
  (défaut 30). ``0`` désactive complètement l'auto-sync Garmin.
- ``DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES`` : période Google Health.
"""

from __future__ import annotations

import datetime as dt
import os

from apscheduler.schedulers.background import BackgroundScheduler

from domestique_ai.api.logging import get_logger
from domestique_ai.api.routers.garmin import trigger_sync_blocking as trigger_garmin_sync
from domestique_ai.config import (
    get_google_health_auto_sync_minutes,
    get_google_health_first_run_delay_minutes,
)
from domestique_ai.healthcheck import ping_healthcheck
from domestique_ai.ingestion.google_health import GoogleHealthClient

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None

_DEFAULT_HEALTHCHECK_INTERVAL_MIN = 5
_DEFAULT_GOOGLE_HEALTH_INTERVAL_MIN = 360
_DEFAULT_GOOGLE_HEALTH_FIRST_RUN_DELAY_MIN = 10
_DEFAULT_GARMIN_INTERVAL_MIN = 30
_DEFAULT_GARMIN_FIRST_RUN_DELAY_MIN = 5


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


def _google_health_auto_sync_interval_minutes() -> int:
    return get_google_health_auto_sync_minutes()


def _google_health_first_run_delay_minutes() -> int:
    return get_google_health_first_run_delay_minutes()


def _google_health_sync_targets() -> list[tuple[str, object]]:
    """Athlètes à synchroniser Google Health : ceux ayant des tokens."""
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, list_users

    users = list_users()
    if not any(u.get("is_bootstrap") for u in users):
        get_or_create_bootstrap_coach()
        users = list_users()

    targets: list[tuple[str, object]] = []
    for user in users:
        ctx = context_for_athlete(user)
        client = GoogleHealthClient.from_tokens_file()
        if client is not None and client.is_authenticated():
            targets.append((user["public_id"], ctx))
    return targets


def _google_health_auto_sync_job() -> None:
    """Job appelé périodiquement pour sync Google Health."""
    try:
        targets = _google_health_sync_targets()
    except Exception:  # noqa: BLE001
        log.exception("Auto-sync Google Health : énumération des athlètes échouée.")
        return

    from domestique_ai.ingestion.google_health import sync_google_health_morning_metrics

    for public_id, ctx in targets:
        try:
            client = GoogleHealthClient.from_tokens_file()
            if client is None:
                continue
            result = sync_google_health_morning_metrics(
                client,
                start_date=dt.date.today() - dt.timedelta(days=7),
                end_date=dt.date.today(),
                db_path=ctx.db_path,
            )
            client.tokens["last_sync_at"] = dt.datetime.now(dt.UTC).isoformat()
            client.save_tokens()
            log.info(
                "Auto-sync Google Health [%s] : %d sync, %d skip.",
                public_id[:8],
                len(result["synced_dates"]),
                len(result["skipped_dates"]),
            )
        except Exception:  # noqa: BLE001
            log.exception("Auto-sync Google Health [%s] : exception non gérée.", public_id[:8])


def _garmin_auto_sync_interval_minutes() -> int:
    return _read_positive_int(
        "DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", _DEFAULT_GARMIN_INTERVAL_MIN
    )


def _garmin_auto_sync_job() -> None:
    """Sync Garmin périodique — cible le propriétaire uniquement.

    Le cache token Garmin Connect est global (``data/.garmin_tokens``) : il
    correspond au compte du propriétaire (bootstrap). Les autres athlètes
    ne sont volontairement pas syncés (ils n'ont pas ce compte Garmin).
    """
    try:
        from domestique_ai.export.garmin_connect import token_cache_present
        from domestique_ai.platform_db import get_or_create_bootstrap_coach, list_users

        if not token_cache_present():
            return
        users = list_users()
        bootstrap = next((u for u in users if u.get("is_bootstrap")), None)
        if bootstrap is None:
            bootstrap = get_or_create_bootstrap_coach()
        if bootstrap is None:
            return

        from domestique_ai.athlete_context import context_for_athlete

        ctx = context_for_athlete(bootstrap)
        key = bootstrap["public_id"]
        if not trigger_garmin_sync(ctx, key):
            log.info("Auto-sync Garmin [%s] : skip (déjà en cours).", key[:8])
    except Exception:  # noqa: BLE001 — un job APScheduler ne doit jamais lever
        log.exception("Auto-sync Garmin : exception non gérée.")


def _healthcheck_interval_minutes() -> int:
    return _read_positive_int("HEALTHCHECKS_PING_INTERVAL_MIN", _DEFAULT_HEALTHCHECK_INTERVAL_MIN)


def _healthcheck_ping_job() -> None:
    """Push un ping vers Healthchecks.io — exceptions toujours capturées."""
    try:
        ping_healthcheck()
    except Exception:  # noqa: BLE001 — best-effort, jamais fatal
        log.exception("Healthcheck ping : exception non gérée.")


def start_scheduler() -> None:
    """Démarre le scheduler s'il n'est pas déjà en cours.

    Enregistre trois jobs indépendants :
    - ``garmin_auto_sync`` : sync Garmin périodique (si activé)
    - ``google_health_auto_sync`` : sync Google Health périodique (si activé)
    - ``healthcheck_ping`` : ping Healthchecks.io périodique (si URL configurée)

    No-op global si déjà démarré. Chaque job est ajouté seulement si sa
    configuration est valide — on peut donc avoir n'importe quelle combinaison.
    """
    global _scheduler
    if _scheduler is not None:
        return

    gh_interval = _google_health_auto_sync_interval_minutes()
    hc_interval = _healthcheck_interval_minutes()
    hc_url_configured = os.getenv("HEALTHCHECKS_PING_URL", "").strip() != ""
    garmin_interval = _garmin_auto_sync_interval_minutes()

    gh_enabled = gh_interval > 0
    hc_enabled = hc_interval > 0 and hc_url_configured
    garmin_enabled = garmin_interval > 0

    if not gh_enabled and not hc_enabled and not garmin_enabled:
        if not gh_enabled:
            log.info("Auto-sync Google Health désactivé.")
        if not hc_url_configured:
            log.info("Healthchecks ping désactivé (HEALTHCHECKS_PING_URL absent).")
        if not garmin_enabled:
            log.info("Auto-sync Garmin désactivé (DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES=0).")
        return

    scheduler = BackgroundScheduler(timezone="UTC")

    if gh_enabled:
        delay = _google_health_first_run_delay_minutes()
        scheduler.add_job(
            _google_health_auto_sync_job,
            "interval",
            minutes=gh_interval,
            id="google_health_auto_sync",
            coalesce=True,
            max_instances=1,
            next_run_time=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=delay),
        )
        log.info(
            "Scheduler : sync Google Health toutes les %d min (1er run dans %d min).",
            gh_interval,
            delay,
        )
    else:
        log.info("Auto-sync Google Health désactivé.")

    if garmin_enabled:
        scheduler.add_job(
            _garmin_auto_sync_job,
            "interval",
            minutes=garmin_interval,
            id="garmin_auto_sync",
            coalesce=True,
            max_instances=1,
            next_run_time=dt.datetime.now(dt.UTC)
            + dt.timedelta(minutes=_DEFAULT_GARMIN_FIRST_RUN_DELAY_MIN),
        )
        log.info("Scheduler : sync Garmin toutes les %d min.", garmin_interval)
    else:
        log.info("Auto-sync Garmin désactivé (DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES=0).")

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
            next_run_time=dt.datetime.now(dt.UTC),
        )
        log.info("Scheduler : ping Healthchecks.io toutes les %d min.", hc_interval)
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
