"""Tests du middleware Bearer (CR-021).

Le middleware est instancié via une mini-app dédiée plutôt qu'en s'appuyant
sur ``domestique_ai.api.main`` : ça permet de contrôler le token sans
dépendre de l'ordre d'import (le middleware capture la valeur au moment de
``add_middleware`` au load du module).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)

    @app.get("/api/secret")
    def _secret() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login")
    def _login() -> dict[str, str]:
        return {"page": "login"}

    @app.get("/")
    def _root() -> dict[str, str]:
        return {"page": "root"}

    return app


@pytest.fixture()
def client_without_token() -> Iterator[TestClient]:
    with TestClient(_make_app(None)) as c:
        yield c


@pytest.fixture()
def client_with_token() -> Iterator[TestClient]:
    with TestClient(_make_app("secret-token-1234")) as c:
        yield c


# ---- Auth désactivée (mode dev) ---------------------------------------------


def test_auth_disabled_lets_api_through(client_without_token: TestClient) -> None:
    r = client_without_token.get("/api/secret")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_auth_disabled_health_works(client_without_token: TestClient) -> None:
    r = client_without_token.get("/api/health")
    assert r.status_code == 200


# ---- Auth activée -----------------------------------------------------------


def test_auth_blocks_api_without_header(client_with_token: TestClient) -> None:
    r = client_with_token.get("/api/secret")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").startswith("Bearer")
    assert r.json() == {"detail": "Unauthorized"}


def test_auth_blocks_api_with_wrong_token(client_with_token: TestClient) -> None:
    r = client_with_token.get("/api/secret", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_auth_blocks_api_with_wrong_scheme(client_with_token: TestClient) -> None:
    r = client_with_token.get("/api/secret", headers={"Authorization": "Basic secret-token-1234"})
    assert r.status_code == 401


def test_auth_accepts_valid_token(client_with_token: TestClient) -> None:
    r = client_with_token.get("/api/secret", headers={"Authorization": "Bearer secret-token-1234"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_exempt_from_auth(client_with_token: TestClient) -> None:
    r = client_with_token.get("/api/health")
    assert r.status_code == 200


def test_static_routes_not_protected(client_with_token: TestClient) -> None:
    """Les routes non-``/api/*`` (bundle PWA, /login, assets) restent libres."""
    r = client_with_token.get("/login")
    assert r.status_code == 200
    r2 = client_with_token.get("/")
    assert r2.status_code == 200


def test_options_preflight_passes_without_token(
    client_with_token: TestClient,
) -> None:
    """Preflight CORS : Starlette les répond, ne pas bloquer en amont."""
    # Pas de CORSMiddleware dans cette mini-app — on vérifie juste qu'on
    # ne renvoie pas 401 sur OPTIONS (le handler downstream peut répondre
    # 405 ou autre, ce qui prouve qu'on l'a laissé passer).
    r = client_with_token.options("/api/secret")
    assert r.status_code != 401


# ---- Token avec whitespace ---------------------------------------------------


def test_auth_token_is_stripped() -> None:
    """Token configuré avec espaces accidentels = token sans espaces."""
    app = _make_app("  padded-token  ")
    with TestClient(app) as c:
        r = c.get("/api/secret", headers={"Authorization": "Bearer padded-token"})
        assert r.status_code == 200


def test_auth_empty_string_treated_as_no_token() -> None:
    """Un token = "" doit comporter comme un token absent (mode dev)."""
    app = _make_app("")
    with TestClient(app) as c:
        r = c.get("/api/secret")
        assert r.status_code == 200
