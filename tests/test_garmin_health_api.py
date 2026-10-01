"""API santé Garmin : statut des sources, préférence de provider, sync manuelle.

Mini-app dédiée, client Garmin mocké (aucun réseau). DB et racine athlètes
isolées par le conftest.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import garmin as garmin_router
from domestique_ai.api.routers import morning as morning_router


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer legacy-token"}


@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(BearerAuthMiddleware, token="legacy-token")
    application.include_router(morning_router.router)
    application.include_router(garmin_router.router)
    garmin_router._health_sync_state.clear()
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def garmin_env(tmp_path: Path, monkeypatch) -> Path:
    """DB bootstrap isolée + credentials Garmin + token dir rempli."""
    db = tmp_path / "bootstrap.db"
    token_dir = tmp_path / "garmin_tokens"
    token_dir.mkdir()
    (token_dir / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("GARMIN_EMAIL", "bob@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(token_dir))
    monkeypatch.delenv("GOOGLE_HEALTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_HEALTH_CLIENT_SECRET", raising=False)
    return db


def test_sources_status_reports_garmin_connected(client: TestClient, garmin_env: Path):
    r = client.get("/api/morning/sources", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "auto"
    assert body["provider_effective"] == "garmin"
    assert body["garmin"]["configured"] is True
    assert body["garmin"]["connected"] is True
    assert body["garmin"]["last_sync_at"] is None
    assert body["google_health"]["configured"] is False
    assert body["google_health"]["connected"] is False


def test_sources_status_fresh_athlete(client: TestClient, tmp_path: Path, monkeypatch):
    """Base athlète neuve + aucun provider connecté → 200, pas d'erreur."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "fresh.db"))
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    monkeypatch.delenv("GARMIN_TOKEN_DIR", raising=False)
    monkeypatch.delenv("GOOGLE_HEALTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_HEALTH_CLIENT_SECRET", raising=False)

    r = client.get("/api/morning/sources", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["garmin"]["connected"] is False
    assert body["google_health"]["connected"] is False
    assert body["provider"] == "auto"
    assert body["provider_effective"] is None


def test_put_provider_preference(client: TestClient, garmin_env: Path):
    r = client.put(
        "/api/morning/sources/provider",
        json={"provider": "google_health"},
        headers=_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "google_health"
    # Google n'est pas connecté : pas de provider effectif.
    assert body["provider_effective"] is None

    back = client.put(
        "/api/morning/sources/provider",
        json={"provider": "auto"},
        headers=_headers(),
    )
    assert back.status_code == 200
    assert back.json()["provider_effective"] == "garmin"


def test_put_provider_invalid_rejected(client: TestClient, garmin_env: Path):
    r = client.put(
        "/api/morning/sources/provider",
        json={"provider": "strava"},
        headers=_headers(),
    )
    assert r.status_code == 422


def test_health_sync_404_without_tokens(client: TestClient, tmp_path: Path, monkeypatch):
    empty_dir = tmp_path / "empty_tokens"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "bootstrap.db"))
    monkeypatch.setenv("GARMIN_EMAIL", "bob@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(empty_dir))
    r = client.post("/api/garmin/health/sync", headers=_headers())
    assert r.status_code == 404


def test_health_sync_clamps_window_and_returns_summary(
    client: TestClient, garmin_env: Path, monkeypatch
):
    import datetime as dt

    from domestique_ai.ingestion import garmin_health

    captured: dict = {}

    def fake_sync(client=None, start_date=None, end_date=None, *, ctx=None, db_path=None):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return {
            "synced_dates": ["2026-05-02"],
            "skipped_dates": ["2026-05-01"],
            "disabled": False,
        }

    monkeypatch.setattr(garmin_health, "sync_garmin_health_morning_metrics", fake_sync)

    r = client.post("/api/garmin/health/sync?days=999", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["synced_dates"] == ["2026-05-02"]
    assert body["message"]

    today = dt.date.today()
    assert captured["end_date"] == today
    assert (today - captured["start_date"]).days == 30


def test_health_sync_conflict_when_already_running(client: TestClient, garmin_env: Path):
    key = str(garmin_env)
    garmin_router._health_sync_state[key] = {"status": "syncing"}
    r = client.post("/api/garmin/health/sync", headers=_headers())
    assert r.status_code == 409


def test_health_sync_502_on_garmin_health_error(client: TestClient, garmin_env: Path, monkeypatch):
    from domestique_ai.ingestion import garmin_health

    def fake_sync(**kwargs):
        raise garmin_health.GarminHealthError("boom")

    monkeypatch.setattr(garmin_health, "sync_garmin_health_morning_metrics", fake_sync)
    r = client.post("/api/garmin/health/sync", headers=_headers())
    assert r.status_code == 502
    assert "boom" in r.json()["detail"]


def test_health_sync_disabled_when_google_preferred(
    client: TestClient, garmin_env: Path, monkeypatch
):
    from domestique_ai.ingestion import garmin_health

    monkeypatch.setattr(
        garmin_health,
        "sync_garmin_health_morning_metrics",
        lambda **kwargs: {"synced_dates": [], "skipped_dates": [], "disabled": True},
    )
    client.put(
        "/api/morning/sources/provider",
        json={"provider": "google_health"},
        headers=_headers(),
    )
    r = client.post("/api/garmin/health/sync", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["disabled"] is True
