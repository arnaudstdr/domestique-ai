"""Tests du panneau d'administration plateforme (``/api/admin/*``).

L'admin est un rôle isolé : il n'accède qu'au panneau, jamais aux droits coach.
On vérifie aussi que ``admin`` n'est pas accessible aux coachs/athlètes, que le
compte propriétaire (bootstrap) ne peut pas être modifié, et que le réglage
``signup_enabled`` pilote bien l'inscription self-service.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai import platform_db as pdb
from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import admin as admin_router
from domestique_ai.api.routers import auth as auth_router

_LEGACY = "legacy-admin-token"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(_make_app()) as c:
        yield c


def _session(role: str) -> tuple[str, str]:
    """Crée un compte du rôle voulu et une session. Retourne (token, public_id)."""
    user = pdb.create_user(role="athlete" if role == "admin" else role)
    if role == "admin":
        user = pdb.set_user_role(user["public_id"], "admin")
    _session_row, token = pdb.create_session(user["id"])
    return token, user["public_id"]


def test_admin_can_list_users(client: TestClient):
    admin_token, admin_pid = _session("admin")
    athlete = pdb.create_user(role="athlete", email="a@b.c")
    r = client.get("/api/admin/users", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    pids = {u["public_id"] for u in r.json()}
    assert {admin_pid, athlete["public_id"]} <= pids


def test_admin_endpoints_forbidden_for_non_admin(client: TestClient):
    coach_token, _ = _session("coach")
    athlete_token, _ = _session("athlete")
    for token in (coach_token, athlete_token, _LEGACY):
        for path in ("/api/admin/users", "/api/admin/feedback", "/api/admin/settings"):
            assert client.get(path, headers=_bearer(token)).status_code == 403, (token, path)


def test_admin_can_change_role(client: TestClient):
    admin_token, _ = _session("admin")
    athlete = pdb.create_user(role="athlete")
    r = client.post(
        f"/api/admin/users/{athlete['public_id']}/role",
        headers=_bearer(admin_token),
        json={"role": "coach"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "coach"
    assert pdb.get_user_by_public_id(athlete["public_id"])["role"] == "coach"


def test_admin_cannot_change_bootstrap_role(client: TestClient):
    admin_token, _ = _session("admin")
    bootstrap = pdb.get_or_create_bootstrap_coach()
    r = client.post(
        f"/api/admin/users/{bootstrap['public_id']}/role",
        headers=_bearer(admin_token),
        json={"role": "athlete"},
    )
    assert r.status_code == 403
    assert pdb.get_user_by_public_id(bootstrap["public_id"])["role"] == "coach"


def test_admin_change_role_unknown_is_404(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.post(
        "/api/admin/users/nope/role",
        headers=_bearer(admin_token),
        json={"role": "coach"},
    )
    assert r.status_code == 404


def test_admin_lists_feedback(client: TestClient):
    admin_token, _ = _session("admin")
    pdb.insert_feedback(category="idea", message="hello", role="athlete")
    r = client.get("/api/admin/feedback", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert any(f["message"] == "hello" for f in r.json())


def test_admin_signup_setting_controls_signup(client: TestClient, monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_SIGNUP_ENABLED", raising=False)
    admin_token, _ = _session("admin")

    # Désactivé par défaut → l'inscription est refusée.
    assert (
        client.post(
            "/api/auth/signup",
            json={"email": "new@example.com", "password": "Sup3rSecret!x", "role": "athlete"},
        ).status_code
        == 403
    )

    # L'admin active l'inscription via l'override en base.
    r = client.put(
        "/api/admin/settings", headers=_bearer(admin_token), json={"signup_enabled": True}
    )
    assert r.status_code == 200 and r.json()["signup_enabled"] is True

    ok = client.post(
        "/api/auth/signup",
        json={"email": "new@example.com", "password": "Sup3rSecret!x", "role": "athlete"},
    )
    assert ok.status_code == 200, ok.text
