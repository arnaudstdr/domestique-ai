"""Auto-sync périodique (Garmin, Google Health, heartbeat) via APScheduler.

Le scheduler tourne dans le process FastAPI (BackgroundScheduler — thread
pool dédié, pas l'event loop asyncio). Chaque job partage le verrou de son
router avec le sync manuel correspondant : pas de risque de chevauchement.

Configuration :
- ``DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES`` : période Garmin en minutes
  (défaut 30). ``0`` désactive complètement l'auto-sync Garmin.
- ``DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES`` : période Google Health.
- ``DOMESTIQUE_AI_DAILY_CHECK_HOUR`` / ``_MINUTE`` : heure du check du matin
  (défaut 08:00 local, ``-1`` = off). ``DOMESTIQUE_AI_SCHEDULER_TZ`` : fuseau
  des CronTrigger (défaut: fuseau système).
- ``DOMESTIQUE_AI_WEEKLY_REVIEW_DAY`` / ``_HOUR`` : revue hebdo (défaut
  dimanche 18h local, ``0`` = off).
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


def _all_athlete_contexts() -> list[tuple[str, object]]:
    """Contexte de tous les athlètes connus (bootstrap + roster)."""
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, list_users

    users = list_users()
    if not any(u.get("is_bootstrap") for u in users):
        get_or_create_bootstrap_coach()
        users = list_users()
    return [(u["public_id"], context_for_athlete(u)) for u in users]


def _weekly_review_job() -> None:
    """Revue hebdomadaire — re-plan adaptatif pour tous les athlètes avec plan actif."""
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.llm.weekly_review import run_weekly_review
    from domestique_ai.notifications import send_pushover
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, list_users

    try:
        users = list_users()
        if not any(u.get("is_bootstrap") for u in users):
            get_or_create_bootstrap_coach()
            users = list_users()
    except Exception:  # noqa: BLE001
        log.exception("Revue hebdo : énumération des athlètes échouée.")
        return

    # Pre-sync Google Health (7 j) — les tendances matin doivent être à jour.
    for public_id, ctx in _google_health_sync_targets():
        try:
            from domestique_ai.ingestion.google_health import sync_google_health_morning_metrics

            client = GoogleHealthClient.from_tokens_file()
            if client is None:
                continue
            sync_google_health_morning_metrics(
                client,
                start_date=dt.date.today() - dt.timedelta(days=7),
                end_date=dt.date.today(),
                db_path=ctx.db_path,
            )
        except Exception:  # noqa: BLE001
            log.warning("Revue hebdo [%s] : pre-sync Google Health échoué.", public_id[:8])

    for user in users:
        public_id = user["public_id"]
        try:
            ctx = context_for_athlete(user)
            result = run_weekly_review(ctx=ctx)
            log.info(
                "Revue hebdo [%s] : decision=%s replanned=%s new_plan_id=%s",
                public_id[:8],
                result.get("decision"),
                result.get("replanned"),
                result.get("new_plan_id"),
            )
            if result.get("replanned") and result.get("new_plan_id"):
                send_pushover(
                    "Plan adapté cette semaine",
                    f"Ajustement {result.get('decision')} — {result.get('reason', '')[:140]}",
                )
        except Exception:  # noqa: BLE001
            log.exception("Revue hebdo [%s] : exception non gérée.", public_id[:8])


def _daily_morning_check_job() -> None:
    """Check du matin (go / alléger / repos), précédé d'un pre-sync frais.

    À l'heure configurée, on rafraîchit d'abord les données du matin
    (Google Health, fenêtre hier→aujourd'hui — si tokens dispo) et les activités
    Garmin, puis on évalue la décision du jour et on la répercute dans le plan
    en cours.

    Chaque athlète est isolé : une exception sur l'un ne doit jamais interrompre
    les autres ni casser le job.
    """
    from domestique_ai.llm.daily_decision import evaluate_daily_decision

    try:
        targets = _all_athlete_contexts()
    except Exception:  # noqa: BLE001
        log.exception("Check du matin : énumération des athlètes échouée.")
        return

    # Pre-sync Google Health (hier → aujourd'hui), idempotent — seulement pour
    # les athlètes avec tokens.
    for public_id, ctx in targets:
        try:
            from domestique_ai.ingestion.google_health import sync_google_health_morning_metrics

            client = GoogleHealthClient.from_tokens_file()
            if client is None:
                continue
            sync_google_health_morning_metrics(
                client,
                start_date=dt.date.today() - dt.timedelta(days=1),
                end_date=dt.date.today(),
                db_path=ctx.db_path,
            )
        except Exception:  # noqa: BLE001
            log.warning("Check du matin [%s] : pre-sync Google Health échoué.", public_id[:8])
    # Pre-sync Garmin (propriétaire) : la séance d'hier soir doit être ingérée.
    try:
        from domestique_ai.athlete_context import context_for_athlete
        from domestique_ai.export.garmin_connect import token_cache_present
        from domestique_ai.platform_db import get_or_create_bootstrap_coach

        if token_cache_present():
            bootstrap = get_or_create_bootstrap_coach()
            if bootstrap is not None:
                ctx = context_for_athlete(bootstrap)
                if not trigger_garmin_sync(ctx, bootstrap["public_id"]):
                    log.info("Check du matin : sync Garmin déjà en cours, skip.")
    except Exception:  # noqa: BLE001
        log.warning("Check du matin : pre-sync Garmin échoué (best-effort).")

    for public_id, ctx in targets:
        try:
            result = evaluate_daily_decision(ctx=ctx)
            log.info(
                "Check du matin [%s] : décision=%s (persisted=%s).",
                public_id[:8],
                result.get("decision"),
                result.get("persisted"),
            )
        except Exception:  # noqa: BLE001
            log.exception("Check du matin [%s] : exception non gérée.", public_id[:8])


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

    # Check du matin (CronTrigger heure locale) — pré-sync GH/Garmin + décision.
    try:
        from apscheduler.triggers.cron import CronTrigger

        from domestique_ai.config import get_daily_check_time, get_scheduler_timezone

        check_time = get_daily_check_time()
        if check_time is not None:
            hour, minute = check_time
            tz = get_scheduler_timezone()
            scheduler.add_job(
                _daily_morning_check_job,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id="daily_morning_check",
                coalesce=True,
                max_instances=1,
            )
            log.info(
                "Scheduler : check du matin à %02d:%02d (%s).",
                hour,
                minute,
                tz,
            )
        else:
            log.info("Check du matin désactivé (DOMESTIQUE_AI_DAILY_CHECK_HOUR=-1).")
    except Exception:  # noqa: BLE001 — un échec de config ne doit pas bloquer le scheduler
        log.exception("Scheduler : échec d'enregistrement du check du matin.")

    # Revue hebdomadaire (CronTrigger heure locale) — re-plan adaptatif.
    try:
        from apscheduler.triggers.cron import CronTrigger

        from domestique_ai.config import get_scheduler_timezone, get_weekly_review_time

        review_time = get_weekly_review_time()
        if review_time is not None:
            weekday, hour = review_time
            tz = get_scheduler_timezone()
            scheduler.add_job(
                _weekly_review_job,
                CronTrigger(day_of_week=weekday, hour=hour, timezone=tz),
                id="weekly_review",
                coalesce=True,
                max_instances=1,
            )
            log.info(
                "Scheduler : revue hebdo le jour %d à %02d:00 (%s).",
                weekday,
                hour,
                tz,
            )
        else:
            log.info("Revue hebdo désactivée (DOMESTIQUE_AI_WEEKLY_REVIEW_DAY<0).")
    except Exception:  # noqa: BLE001
        log.exception("Scheduler : échec d'enregistrement de la revue hebdo.")

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
