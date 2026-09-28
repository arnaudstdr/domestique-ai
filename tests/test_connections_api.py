"""API connexions par athlète : Garmin (connect/connect-mfa/disconnect) + GH.

Mini-app dédiée, mocks complets (pas de réseau, pas de vrai login Garmin).
La DB plateforme et la racine athlètes sont isolées par le conftest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import garmin as garmin_router
from domestique_ai.api.routers import google_health as gh_router


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer legacy-token"}


@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(BearerAuthMiddleware, token="legacy-token")
    application.include_router(garmin_router.router)
    application.include_router(gh_router.router)
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


class _FakeGarminClient:
    """Client garminconnect simulé pour ``start_login`` / ``finish_login``."""

    def __init__(self) -> None:
        self.client = self
        self.resumed_with: str | None = None

    def resume_login(self, client_state, code):
        if code != "123456":
            raise RuntimeError("invalid mfa code")
        self.resumed_with = code
        return (None, None)

    def dump(self, path):
        return None


def test_garmin_status_default_not_connected(client: TestClient, monkeypatch):
    # Le .env du dev peut contenir des credentials Garmin : on part d'un état propre.
    monkeypatch.setenv("GARMIN_EMAIL", "")
    monkeypatch.setenv("GARMIN_PASSWORD", "")
    r = client.get("/api/garmin/status", headers=_headers())
    assert r.status_code == 200
    assert r.json()["connected"] is False
    assert r.json()["credentials"] is False


def test_connect_direct_success_persists_credentials(client: TestClient, monkeypatch):
    from domestique_ai.export import garmin_connect as gc
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, get_user_by_public_id

    fake = _FakeGarminClient()
    monkeypatch.setattr(gc, "start_login", lambda email, password, token_dir: ("connected", fake))

    r = client.post(
        "/api/garmin/connect",
        json={"email": "bob@example.com", "password": "pw"},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "connected"

    coach = get_or_create_bootstrap_coach()
    assert get_user_by_public_id(coach["public_id"])["garmin_email"] == "bob@example.com"


def test_connect_mfa_flow_then_submit(client: TestClient, monkeypatch):
    from domestique_ai.export import garmin_connect as gc

    fake = _FakeGarminClient()
    monkeypatch.setattr(
        gc, "start_login", lambda email, password, token_dir: ("mfa_required", fake)
    )

    r = client.post(
        "/api/garmin/connect",
        json={"email": "bob@example.com", "password": "pw"},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "mfa_required"

    bad = client.post("/api/garmin/connect/mfa", json={"code": "000000"}, headers=_headers())
    assert bad.status_code == 400

    ok = client.post("/api/garmin/connect/mfa", json={"code": "123456"}, headers=_headers())
    assert ok.status_code == 200
    assert ok.json()["status"] == "connected"
    assert fake.resumed_with == "123456"


def test_submit_mfa_without_pending_returns_409(client: TestClient):
    r = client.post("/api/garmin/connect/mfa", json={"code": "123456"}, headers=_headers())
    assert r.status_code == 409


def test_connect_requires_email_and_password(client: TestClient):
    r = client.post(
        "/api/garmin/connect",
        json={"email": "", "password": ""},
        headers=_headers(),
    )
    assert r.status_code == 422


def test_disconnect_clears_credentials(client: TestClient, monkeypatch):
    from domestique_ai.export import garmin_connect as gc
    from domestique_ai.platform_db import get_or_create_bootstrap_coach, get_user_garmin_credentials

    fake = _FakeGarminClient()
    monkeypatch.setattr(gc, "start_login", lambda email, password, token_dir: ("connected", fake))
    client.post(
        "/api/garmin/connect",
        json={"email": "bob@example.com", "password": "pw"},
        headers=_headers(),
    )
    coach = get_or_create_bootstrap_coach()
    assert get_user_garmin_credentials(coach["id"]) == ("bob@example.com", "pw")

    r = client.post("/api/garmin/disconnect", headers=_headers())
    assert r.status_code == 204
    assert get_user_garmin_credentials(coach["id"]) == (None, None)


def _athlete_ctx(public_id: str):
    from domestique_ai.athlete_context import AthleteContext
    from domestique_ai.config import get_athletes_root

    root = get_athletes_root() / public_id
    return AthleteContext(
        db_path=root / "strava_activities.db",
        profile_path=root / "profile.yaml",
        objective_path=root / "objective.yaml",
        availability_path=root / "availability.yaml",
        ftp=250.0,
        hr_rest=None,
        hr_max=None,
        sex="M",
        lthr_pct=0.88,
        public_id=public_id,
    )


def test_google_health_tokens_isolated_per_athlete(client: TestClient):
    """Un fichier de tokens d'athlète n'affecte pas le statut du bootstrap."""
    from domestique_ai.config import google_health_tokens_path_for

    path = google_health_tokens_path_for(_athlete_ctx("deadbeef"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"access_token": "x", "refresh_token": "y"}', encoding="utf-8")

    r = client.get("/api/google-health/status", headers=_headers())
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_garmin_status_needs_reauth_on_auth_error(client: TestClient, monkeypatch):
    from domestique_ai.api.routers import garmin as garmin_module
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.export import garmin_connect as gc
    from domestique_ai.platform_db import get_or_create_bootstrap_coach

    monkeypatch.setenv("GARMIN_EMAIL", "bob@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")
    monkeypatch.setattr(gc, "token_cache_present", lambda token_dir=None: True)

    coach = get_or_create_bootstrap_coach()
    key = str(context_for_athlete(coach).db_path)
    garmin_module._set_state(key, status="error", error="Authentication failed (401)")

    r = client.get("/api/garmin/status", headers=_headers())
    assert r.status_code == 200
    assert r.json()["needs_reauth"] is True


def test_garmin_status_needs_reauth_false_on_network_error(client: TestClient, monkeypatch):
    from domestique_ai.api.routers import garmin as garmin_module
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_or_create_bootstrap_coach

    coach = get_or_create_bootstrap_coach()
    key = str(context_for_athlete(coach).db_path)
    garmin_module._set_state(key, status="error", error="Connection timed out")

    r = client.get("/api/garmin/status", headers=_headers())
    assert r.json()["needs_reauth"] is False


def test_garmin_status_orphan_tokens_flag(client: TestClient, monkeypatch, tmp_path):
    from domestique_ai.platform_db import get_or_create_bootstrap_coach

    global_dir = tmp_path / "global_tokens"
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(global_dir))
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "t.json").write_text("{}", encoding="utf-8")

    coach = get_or_create_bootstrap_coach()
    # Le contexte bootstrap a public_id="" → orphan_tokens toujours False.
    r = client.get("/api/garmin/status", headers=_headers())
    assert r.json()["orphan_tokens"] is False

    # Un athlète ciblé sans token propre → True.
    r2 = client.get(f"/api/garmin/status?athlete={coach['public_id']}", headers=_headers())
    assert r2.status_code == 200
