"""Tests de l'auto-sync Garmin (verrou partagé + configuration scheduler)."""

from __future__ import annotations

import pytest

from domestique_ai.api import scheduler
from domestique_ai.api.routers import garmin as garmin_router

_CTX = object()  # sentinelle : _run_sync est mocké, le ctx réel n'importe pas


@pytest.fixture(autouse=True)
def _reset_sync_state(monkeypatch):
    """Vide le state (indexé par athlète) + neutralise les vars d'env scheduler."""
    # Ces tests doivent être indépendants de la config réelle du dev.
    for key in (
        "DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES",
        "DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES",
        "DOMESTIQUE_AI_GOOGLE_HEALTH_FIRST_RUN_DELAY_MIN",
        "HEALTHCHECKS_PING_URL",
        "HEALTHCHECKS_PING_INTERVAL_MIN",
    ):
        monkeypatch.delenv(key, raising=False)

    garmin_router._sync_state.clear()
    yield
    garmin_router._sync_state.clear()


# ---- _claim_sync / trigger_sync_blocking (par athlète) ----------------------


def test_claim_sync_passes_through_idle_state():
    assert garmin_router._claim_sync("alice") is True
    assert garmin_router._sync_state["alice"]["status"] == "syncing"


def test_claim_sync_refuses_concurrent_call_same_athlete():
    assert garmin_router._claim_sync("alice") is True
    assert garmin_router._claim_sync("alice") is False


def test_claim_sync_isolated_between_athletes():
    # Un sync en cours pour A ne bloque pas B.
    assert garmin_router._claim_sync("alice") is True
    assert garmin_router._claim_sync("bob") is True


def test_trigger_sync_blocking_skips_when_busy(monkeypatch):
    garmin_router._sync_state["alice"] = {"status": "syncing"}
    called = {"n": 0}

    def fake_run_sync(ctx, key) -> None:
        called["n"] += 1

    monkeypatch.setattr(garmin_router, "_run_sync", fake_run_sync)
    assert garmin_router.trigger_sync_blocking(_CTX, "alice") is False
    assert called["n"] == 0


def test_trigger_sync_blocking_executes_when_idle(monkeypatch):
    called = {"n": 0}

    def fake_run_sync(ctx, key) -> None:
        called["n"] += 1
        garmin_router._set_state(key, status="done")

    monkeypatch.setattr(garmin_router, "_run_sync", fake_run_sync)
    assert garmin_router.trigger_sync_blocking(_CTX, "alice") is True
    assert called["n"] == 1


# ---- Configuration scheduler ------------------------------------------------


def test_interval_default(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", raising=False)
    assert scheduler._garmin_auto_sync_interval_minutes() == 30


def test_interval_custom(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "15")
    assert scheduler._garmin_auto_sync_interval_minutes() == 15


def test_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    assert scheduler._garmin_auto_sync_interval_minutes() == 0


def test_interval_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "pas-un-nombre")
    assert scheduler._garmin_auto_sync_interval_minutes() == 30


def test_interval_negative_clamped_to_zero(monkeypatch):
    # Une valeur négative ne déclencherait rien d'utile — on clampe à 0
    # (auto-sync désactivé) plutôt que de laisser APScheduler hurler.
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "-5")
    assert scheduler._garmin_auto_sync_interval_minutes() == 0


def test_start_scheduler_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_start_stop_scheduler_lifecycle(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "30")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        assert scheduler._scheduler is not None
        # Idempotent : un 2e appel ne crée pas un second scheduler.
        first = scheduler._scheduler
        scheduler.start_scheduler()
        assert scheduler._scheduler is first
    finally:
        scheduler.stop_scheduler()
    assert scheduler._scheduler is None


def test_google_health_interval_default(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", raising=False)
    assert scheduler._google_health_auto_sync_interval_minutes() == 360


def test_google_health_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    assert scheduler._google_health_auto_sync_interval_minutes() == 0


def test_google_health_start_scheduler_lifecycle(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "60")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_FIRST_RUN_DELAY_MIN", "60")
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        assert scheduler._scheduler is not None
        job = scheduler._scheduler.get_job("google_health_auto_sync")
        assert job is not None
    finally:
        scheduler.stop_scheduler()
    assert scheduler._scheduler is None


def test_garmin_auto_sync_job_triggers_bootstrap(monkeypatch):
    """Le job doit appeler trigger_sync_blocking une fois pour le bootstrap."""
    import tempfile
    from pathlib import Path

    import domestique_ai.config
    from domestique_ai import athlete_context, platform_db
    from domestique_ai.athlete_context import AthleteContext

    users = [{"public_id": "owner", "is_bootstrap": True, "role": "coach"}]
    monkeypatch.setattr(platform_db, "list_users", lambda path=None: users)
    monkeypatch.setattr(platform_db, "get_or_create_bootstrap_coach", lambda: users[0])

    ctx = AthleteContext(
        db_path=Path("/tmp/owner.db"),
        profile_path=Path("/tmp/p.yaml"),
        objective_path=Path("/tmp/o.yaml"),
        availability_path=Path("/tmp/a.yaml"),
        ftp=250.0,
        hr_rest=None,
        hr_max=None,
        sex="M",
        lthr_pct=0.88,
    )
    monkeypatch.setattr(athlete_context, "context_for_athlete", lambda user: ctx)

    # Cache token Garmin présent (dossier temporaire vide suffit : on mocke).
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(domestique_ai.config, "get_garmin_token_dir", lambda: Path(td))
        monkeypatch.setattr("domestique_ai.export.garmin_connect.token_cache_present", lambda: True)
        seen: list[str] = []

        def fake_trigger(ctx_, key) -> bool:
            seen.append(key)
            return True

        monkeypatch.setattr(scheduler, "trigger_garmin_sync", fake_trigger)
        scheduler._garmin_auto_sync_job()
        assert seen == ["owner"]


def test_garmin_auto_sync_job_swallows_enumeration_errors(monkeypatch):
    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("domestique_ai.platform_db.list_users", boom)
    scheduler._garmin_auto_sync_job()  # ne doit pas lever


# ---- Healthcheck ping job ---------------------------------------------------


def test_healthcheck_interval_default(monkeypatch):
    monkeypatch.delenv("HEALTHCHECKS_PING_INTERVAL_MIN", raising=False)
    assert scheduler._healthcheck_interval_minutes() == 5


def test_healthcheck_interval_custom(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_INTERVAL_MIN", "10")
    assert scheduler._healthcheck_interval_minutes() == 10


def test_healthcheck_ping_job_swallows_exceptions(monkeypatch):
    """Toute exception remontant de ping_healthcheck doit être avalée."""

    def boom() -> bool:
        raise RuntimeError("dns down")

    monkeypatch.setattr(scheduler, "ping_healthcheck", boom)
    scheduler._healthcheck_ping_job()  # ne doit pas lever


def test_scheduler_registers_only_sync_when_healthcheck_disabled(monkeypatch):
    """HC désactivé (URL absente) — seul le job sync est enregistré."""
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "30")
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        assert scheduler._scheduler is not None
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "garmin_auto_sync" in jobs
        assert "healthcheck_ping" not in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_both_jobs_when_configured(monkeypatch):
    """Sync + HC activés — les deux jobs sont enregistrés."""
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "30")
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123")
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "garmin_auto_sync" in jobs
        assert "healthcheck_ping" in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_only_healthcheck_when_sync_disabled(monkeypatch):
    """Sync désactivé (interval=0) mais HC activé — seul le ping tourne."""
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123")
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        # Le scheduler doit exister puisqu'on a un job à faire tourner.
        assert scheduler._scheduler is not None
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "garmin_auto_sync" not in jobs
        assert "healthcheck_ping" in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_noop_when_everything_disabled(monkeypatch):
    """Tout désactivé — aucun scheduler créé."""
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None
