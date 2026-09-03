"""Tests de l'auto-sync Strava (verrou partagé + configuration scheduler)."""

from __future__ import annotations

import pytest

from domestique_ai.api import scheduler
from domestique_ai.api.routers import strava as strava_router

_CTX = object()  # sentinelle : _run_sync est mocké, le ctx réel n'importe pas


@pytest.fixture(autouse=True)
def _reset_sync_state(monkeypatch):
    """Vide le state (indexé par athlète) + neutralise les vars d'env scheduler."""
    # Ces tests doivent être indépendants de la config réelle du dev.
    for key in (
        "DOMESTIQUE_AI_AUTO_SYNC_MINUTES",
        "DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN",
        "DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES",
        "DOMESTIQUE_AI_GOOGLE_HEALTH_FIRST_RUN_DELAY_MIN",
        "HEALTHCHECKS_PING_URL",
        "HEALTHCHECKS_PING_INTERVAL_MIN",
    ):
        monkeypatch.delenv(key, raising=False)

    strava_router._sync_state.clear()
    yield
    strava_router._sync_state.clear()


# ---- _claim_sync / trigger_sync_blocking (par athlète) ----------------------


def test_claim_sync_passes_through_idle_state():
    assert strava_router._claim_sync("alice") is True
    assert strava_router._sync_state["alice"]["status"] == "syncing"


def test_claim_sync_refuses_concurrent_call_same_athlete():
    assert strava_router._claim_sync("alice") is True
    assert strava_router._claim_sync("alice") is False


def test_claim_sync_isolated_between_athletes():
    # Un sync en cours pour A ne bloque pas B.
    assert strava_router._claim_sync("alice") is True
    assert strava_router._claim_sync("bob") is True


def test_trigger_sync_blocking_skips_when_busy(monkeypatch):
    strava_router._sync_state["alice"] = {"status": "syncing"}
    called = {"n": 0}

    def fake_run_sync(ctx, key, *, user=None) -> None:
        called["n"] += 1

    monkeypatch.setattr(strava_router, "_run_sync", fake_run_sync)
    assert strava_router.trigger_sync_blocking(_CTX, "alice") is False
    assert called["n"] == 0


def test_trigger_sync_blocking_executes_when_idle(monkeypatch):
    called = {"n": 0}

    def fake_run_sync(ctx, key, *, user=None) -> None:
        called["n"] += 1
        strava_router._set_state(key, status="done")

    monkeypatch.setattr(strava_router, "_run_sync", fake_run_sync)
    assert strava_router.trigger_sync_blocking(_CTX, "alice") is True
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
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_start_stop_scheduler_lifecycle(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "30")
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
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


def test_google_health_interval_default(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", raising=False)
    assert scheduler._google_health_auto_sync_interval_minutes() == 360


def test_google_health_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    assert scheduler._google_health_auto_sync_interval_minutes() == 0


def test_google_health_start_scheduler_lifecycle(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AUTO_SYNC_MINUTES", "0")
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


def test_sync_targets_includes_tokened_skips_others(tmp_path, monkeypatch):
    """_sync_targets retient les athlètes ayant un fichier tokens, saute les autres."""
    from domestique_ai import athlete_context, platform_db
    from domestique_ai.athlete_context import AthleteContext

    users = [
        {"public_id": "owner", "is_bootstrap": True, "role": "coach"},
        {"public_id": "alice", "is_bootstrap": False, "role": "athlete"},
        {"public_id": "bob", "is_bootstrap": False, "role": "athlete"},
    ]
    monkeypatch.setattr(platform_db, "list_users", lambda path=None: users)

    def fake_ctx(user: dict) -> AthleteContext:
        root = tmp_path / user["public_id"]
        return AthleteContext(
            db_path=root / "x.db",
            tokens_path=root / ".tok",
            profile_path=root / "p.yaml",
            objective_path=root / "o.yaml",
            availability_path=root / "a.yaml",
            ftp=250.0,
            hr_rest=None,
            hr_max=None,
            sex="M",
            lthr_pct=0.88,
        )

    monkeypatch.setattr(athlete_context, "context_for_athlete", fake_ctx)
    # owner + alice ont des tokens, bob non.
    for pid in ("owner", "alice"):
        d = tmp_path / pid
        d.mkdir(parents=True)
        (d / ".tok").write_text("{}")

    targets = scheduler._sync_targets()
    assert [u["public_id"] for u, _ in targets] == ["owner", "alice"]


def test_auto_sync_job_triggers_each_target(monkeypatch):
    """Le job doit appeler trigger_sync_blocking une fois par athlète cible."""
    targets = [
        ({"public_id": "alice"}, _CTX),
        ({"public_id": "bob"}, _CTX),
    ]
    monkeypatch.setattr(scheduler, "_sync_targets", lambda: targets)
    seen: list[str] = []

    def fake_trigger(ctx, key, *, user=None) -> bool:
        seen.append(key)
        return True

    monkeypatch.setattr(scheduler, "trigger_sync_blocking", fake_trigger)
    scheduler._auto_sync_job()
    assert seen == ["alice", "bob"]


def test_auto_sync_job_swallows_target_enumeration_errors(monkeypatch):
    def boom() -> list:
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "_sync_targets", boom)
    scheduler._auto_sync_job()  # ne doit pas lever


def test_auto_sync_job_isolates_per_athlete_errors(monkeypatch):
    targets = [({"public_id": "alice"}, _CTX), ({"public_id": "bob"}, _CTX)]
    monkeypatch.setattr(scheduler, "_sync_targets", lambda: targets)
    seen: list[str] = []

    def fake_trigger(ctx, key, *, user=None) -> bool:
        seen.append(key)
        if key == "alice":
            raise RuntimeError("alice KO")
        return True

    monkeypatch.setattr(scheduler, "trigger_sync_blocking", fake_trigger)
    scheduler._auto_sync_job()  # ne doit pas lever
    # bob est quand même traité malgré l'échec d'alice.
    assert seen == ["alice", "bob"]


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
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123")
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
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123")
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
    monkeypatch.setenv("DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES", "0")
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_garmin_interval_default(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", raising=False)
    assert scheduler._garmin_auto_sync_interval_minutes() == 30


def test_garmin_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES", "0")
    assert scheduler._garmin_auto_sync_interval_minutes() == 0


def test_garmin_auto_sync_job_skips_silently_without_tokens(monkeypatch):
    """Sans cache token Garmin, le job ne fait rien et ne lève pas."""
    monkeypatch.setattr(scheduler, "trigger_garmin_sync", lambda ctx, key: True)
    # token_cache_present() → False via get_garmin_token_dir vide.
    monkeypatch.setenv("GARMIN_TOKEN_DIR", "")
    import tempfile
    from domestique_ai.config import get_garmin_token_dir

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(
            "domestique_ai.config.get_garmin_token_dir", lambda: __import__("pathlib").Path(td)
        )
        scheduler._garmin_auto_sync_job()  # ne doit ni lever ni syncer
