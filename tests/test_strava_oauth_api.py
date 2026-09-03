"""Tests de l'onboarding Strava web par athlète (palier 1c)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import strava as strava_router

_LEGACY = "legacy-oauth-token"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    app.include_router(strava_router.router)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def env(tmp_path: Path, monkeypatch) -> Iterator[dict]:
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "https://example.test/api/strava/callback")
    monkeypatch.delenv("DOMESTIQUE_AI_APP_BASE_URL", raising=False)
    # La 1re sync ne doit pas réellement tourner pendant les tests.
    monkeypatch.setattr(strava_router, "_run_sync", lambda *a, **k: None)
    with TestClient(_make_app()) as c:
        yield {"client": c, "root": tmp_path / "athletes"}


def _new_athlete(client: TestClient) -> tuple[str, str]:
    inv = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    acc = client.post("/api/auth/accept-invite", json={"invite_token": inv.json()["invite_token"]})
    session = acc.json()["session_token"]
    pid = client.get("/api/auth/me", headers=_bearer(session)).json()["public_id"]
    return session, pid


def test_authorize_requires_auth(env):
    assert env["client"].get("/api/strava/authorize").status_code == 401


def test_authorize_returns_url_with_state(env):
    c = env["client"]
    sess, _ = _new_athlete(c)
    r = c.get("/api/strava/authorize", headers=_bearer(sess))
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["client-123"]
    assert qs["state"] and qs["state"][0]


def test_callback_connects_and_writes_tokens(env, monkeypatch):
    c, root = env["client"], env["root"]
    sess, pid = _new_athlete(c)

    # Avant connexion.
    assert c.get("/api/strava/connection", headers=_bearer(sess)).json() == {"connected": False}

    # Récupère un state valide via authorize.
    url = c.get("/api/strava/authorize", headers=_bearer(sess)).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]

    monkeypatch.setattr(
        strava_router.StravaClient,
        "exchange_code_for_token",
        lambda *a, **k: {
            "access_token": "acc",
            "refresh_token": "ref",
            "expires_at": 9_999_999_999,
        },
    )

    r = c.get(
        "/api/strava/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/?strava=connected"
    # Tokens écrits dans l'espace de l'athlète.
    assert (root / pid / ".strava_tokens.json").exists()
    # connection reflète l'état.
    assert c.get("/api/strava/connection", headers=_bearer(sess)).json() == {"connected": True}


def test_callback_rejects_replayed_state(env, monkeypatch):
    c = env["client"]
    sess, _ = _new_athlete(c)
    url = c.get("/api/strava/authorize", headers=_bearer(sess)).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr(
        strava_router.StravaClient,
        "exchange_code_for_token",
        lambda *a, **k: {"access_token": "a", "refresh_token": "r", "expires_at": 1},
    )
    first = c.get(
        "/api/strava/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert first.headers["location"] == "/?strava=connected"
    # Rejeu du même state → erreur.
    second = c.get(
        "/api/strava/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert second.status_code == 302
    assert second.headers["location"] == "/?strava=error"


def test_callback_invalid_state_redirects_error(env):
    c = env["client"]
    r = c.get(
        "/api/strava/callback",
        params={"code": "c", "state": "bogus"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/?strava=error"


def test_callback_is_public(env):
    # Joignable sans header Bearer (exempté) — pas de 401.
    r = env["client"].get(
        "/api/strava/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/?strava=error"
