"""Tests de l'auto-sync Strava (verrou partagé + configuration scheduler)."""

from __future__ import annotations

import pytest

from domestique_ai.api import scheduler
from domestique_ai.api.routers import strava as strava_router


@pytest.fixture(autouse=True)
def _reset_sync_state(monkeypatch):
    """Remet le state global à idle + neutralise les vars d'env du scheduler."""
    # Ces tests doivent être indépendants de la config réelle du dev.
    for key in (
        "DOMESTIQUE_AI_AUTO_SYNC_MINUTES",
        "DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN",
        "HEALTHCHECKS_PING_URL",
        "HEALTHCHECKS_PING_INTERVAL_MIN",
    ):
        monkeypatch.delenv(key, raising=False)

    strava_router._sync_state.update(
        status="idle",
        inserted=None,
        error=None,
        started_at=None,
        finished_at=None,
    )
    yield
    strava_router._sync_state.update(
        status="idle",
        inserted=None,
        error=None,
        started_at=None,
        finished_at=None,
    )


# ---- _claim_sync / trigger_sync_blocking ------------------------------------


def test_claim_sync_passes_through_idle_state():
    assert strava_router._claim_sync() is True
    assert strava_router._sync_state["status"] == "syncing"


def test_claim_sync_refuses_concurrent_call():
    # 1er claim réussit, le 2e doit échouer tant que l'état est "syncing".
    assert strava_router._claim_sync() is True
    assert strava_router._claim_sync() is False


def test_trigger_sync_blocking_skips_when_busy(monkeypatch):
    # Simule un sync en cours : trigger doit retourner False sans appeler
    # _run_sync.
    strava_router._sync_state["status"] = "syncing"
    called = {"n": 0}

    def fake_run_sync() -> None:
        called["n"] += 1

    monkeypatch.setattr(strava_router, "_run_sync", fake_run_sync)
    assert strava_router.trigger_sync_blocking() is False
    assert called["n"] == 0


def test_trigger_sync_blocking_executes_when_idle(monkeypatch):
    called = {"n": 0}

    def fake_run_sync() -> None:
        called["n"] += 1
        # Simule la fin du sync (état done) pour refléter ce que ferait
        # le vrai _run_sync.
        strava_router._sync_state["status"] = "done"

    monkeypatch.setattr(strava_router, "_run_sync", fake_run_sync)
    assert strava_router.trigger_sync_blocking() is True
    assert called["n"] == 1


# ---- Configuration scheduler ------------------------------------------------


def test_interval_default(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", raising=False)
    assert scheduler._auto_sync_interval_minutes() == 30


def test_interval_custom(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "15")
    assert scheduler._auto_sync_interval_minutes() == 15


def test_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "0")
    assert scheduler._auto_sync_interval_minutes() == 0


def test_interval_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "pas-un-nombre")
    assert scheduler._auto_sync_interval_minutes() == 30


def test_interval_negative_clamped_to_zero(monkeypatch):
    # Une valeur négative ne déclencherait rien d'utile — on clampe à 0
    # (auto-sync désactivé) plutôt que de laisser APScheduler hurler.
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "-5")
    assert scheduler._auto_sync_interval_minutes() == 0


def test_start_scheduler_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "0")
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_start_stop_scheduler_lifecycle(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "30")
    # Délai de 1er run élevé pour ne pas déclencher de job pendant le test.
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN", "60")
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


def test_auto_sync_job_delegates_to_trigger(monkeypatch):
    """Le job du scheduler doit appeler trigger_sync_blocking."""
    calls = {"n": 0}

    def fake_trigger() -> bool:
        calls["n"] += 1
        return False  # simule "déjà en cours" pour rester safe

    monkeypatch.setattr(scheduler, "trigger_sync_blocking", fake_trigger)
    scheduler._auto_sync_job()
    assert calls["n"] == 1


def test_auto_sync_job_swallows_exceptions(monkeypatch):
    """Un APScheduler job qui lève marque le job comme erroné et arrête le
    scheduler. On s'assure que _auto_sync_job avale toute exception."""

    def boom() -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "trigger_sync_blocking", boom)
    scheduler._auto_sync_job()  # ne doit pas lever


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
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "30")
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN", "60")
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        assert scheduler._scheduler is not None
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "strava_auto_sync" in jobs
        assert "healthcheck_ping" not in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_both_jobs_when_configured(monkeypatch):
    """Sync + HC activés — les deux jobs sont enregistrés."""
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "30")
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN", "60")
    monkeypatch.setenv(
        "HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123"
    )
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "strava_auto_sync" in jobs
        assert "healthcheck_ping" in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_only_healthcheck_when_sync_disabled(monkeypatch):
    """Sync désactivé (interval=0) mais HC activé — seul le ping tourne."""
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv(
        "HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123"
    )
    scheduler._scheduler = None
    try:
        scheduler.start_scheduler()
        # Le scheduler doit exister puisqu'on a un job à faire tourner.
        assert scheduler._scheduler is not None
        jobs = {j.id for j in scheduler._scheduler.get_jobs()}
        assert "strava_auto_sync" not in jobs
        assert "healthcheck_ping" in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_noop_when_everything_disabled(monkeypatch):
    """Tout désactivé — aucun scheduler créé."""
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "0")
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None
