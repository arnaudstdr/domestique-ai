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
from domestique_ai.api.routers import announcement as announcement_router
from domestique_ai.api.routers import auth as auth_router

_LEGACY = "legacy-admin-token"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(announcement_router.router)
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


def test_admin_can_reset_2fa(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="coach", email="target@example.com")
    pdb.set_totp_secret(target["id"], "SECRET123")
    pdb.enable_totp(target["id"])
    assert pdb.get_user_credentials(target["id"])["totp_enabled"] is True

    r = client.post(
        f"/api/admin/users/{target['public_id']}/reset-2fa",
        headers=_bearer(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["totp_enabled"] is False
    assert pdb.get_user_credentials(target["id"])["totp_enabled"] is False


def test_reset_2fa_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    target = pdb.create_user(role="coach")
    r = client.post(
        f"/api/admin/users/{target['public_id']}/reset-2fa",
        headers=_bearer(athlete_token),
    )
    assert r.status_code == 403


def test_reset_2fa_unknown_is_404(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.post("/api/admin/users/nope/reset-2fa", headers=_bearer(admin_token))
    assert r.status_code == 404


def test_admin_lists_feedback(client: TestClient):
    admin_token, _ = _session("admin")
    pdb.insert_feedback(category="idea", message="hello", role="athlete")
    r = client.get("/api/admin/feedback", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert any(f["message"] == "hello" for f in r.json())


def test_admin_can_change_feedback_status(client: TestClient):
    admin_token, _ = _session("admin")
    entry = pdb.insert_feedback(category="idea", message="hi")
    assert entry["status"] == "new"
    for status in ("acknowledged", "done", "rejected", "new"):
        r = client.patch(
            f"/api/admin/feedback/{entry['id']}",
            headers=_bearer(admin_token),
            json={"status": status},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
    assert pdb.list_feedback()[0]["status"] == "new"


def test_feedback_status_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    entry = pdb.insert_feedback(category="bug", message="x")
    r = client.patch(
        f"/api/admin/feedback/{entry['id']}",
        headers=_bearer(athlete_token),
        json={"status": "done"},
    )
    assert r.status_code == 403


def test_feedback_status_unknown_is_404(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.patch(
        "/api/admin/feedback/999999",
        headers=_bearer(admin_token),
        json={"status": "done"},
    )
    assert r.status_code == 404


def test_feedback_status_invalid_is_422(client: TestClient):
    admin_token, _ = _session("admin")
    entry = pdb.insert_feedback(category="idea", message="x")
    r = client.patch(
        f"/api/admin/feedback/{entry['id']}",
        headers=_bearer(admin_token),
        json={"status": "nope"},
    )
    assert r.status_code == 422


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


# --- Fiche compte + actions ---------------------------------------------------


def test_admin_user_detail_includes_security_and_links(client: TestClient):
    admin_token, _ = _session("admin")
    coach = pdb.create_user(role="coach", email="coach@example.com")
    athlete = pdb.create_user(role="athlete", email="ath@example.com")
    pdb.link_coach_athlete(coach["id"], athlete["id"])

    r = client.get(f"/api/admin/users/{athlete['public_id']}", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "athlete"
    assert body["locked"] is False
    assert body["n_activities"] == 0
    assert body["last_activity_date"] is None
    assert [c["public_id"] for c in body["coaches"]] == [coach["public_id"]]
    assert body["athletes_count"] == 0
    # Jamais de secret dans la fiche.
    assert "password_hash" not in body and "totp_secret" not in body


def test_admin_user_detail_unknown_is_404(client: TestClient):
    admin_token, _ = _session("admin")
    assert client.get("/api/admin/users/nope", headers=_bearer(admin_token)).status_code == 404


def test_admin_can_unlock_account(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="coach", email="lock@example.com")
    for _ in range(pdb.MAX_FAILED_ATTEMPTS):
        pdb.record_failed_login(target["id"])
    assert pdb.user_is_locked(pdb.get_user_credentials(target["id"])["locked_until"]) is True

    r = client.post(f"/api/admin/users/{target['public_id']}/unlock", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["locked"] is False
    assert pdb.get_user_credentials(target["id"])["failed_attempts"] == 0


def test_admin_can_verify_email(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete", email="v@example.com", email_verified=False)
    assert pdb.get_user_by_public_id(target["public_id"])["email_verified"] is False

    r = client.post(
        f"/api/admin/users/{target['public_id']}/verify-email", headers=_bearer(admin_token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["email_verified"] is True


def test_admin_password_reset_requires_email(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete")  # pas d'email
    r = client.post(
        f"/api/admin/users/{target['public_id']}/password-reset", headers=_bearer(admin_token)
    )
    assert r.status_code == 400


def test_admin_password_reset_sends_link(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete", email="reset@example.com", password_hash="h")
    r = client.post(
        f"/api/admin/users/{target['public_id']}/password-reset", headers=_bearer(admin_token)
    )
    assert r.status_code == 200, r.text
    # Sans SMTP configuré, l'envoi est best-effort : la clé existe mais vaut False.
    assert r.json()["sent"] is False
    # Le compte existe toujours (aucune suppression surprise).
    assert pdb.get_user_by_email("reset@example.com") is not None


def test_admin_sessions_and_logout(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete")
    pdb.create_session(target["id"])
    pdb.create_session(target["id"])

    r = client.get(f"/api/admin/users/{target['public_id']}/sessions", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2
    assert "token_hash" not in r.json()[0]

    out = client.post(
        f"/api/admin/users/{target['public_id']}/logout", headers=_bearer(admin_token)
    )
    assert out.status_code == 200 and out.json()["revoked"] == 2
    assert (
        client.get(
            f"/api/admin/users/{target['public_id']}/sessions", headers=_bearer(admin_token)
        ).json()
        == []
    )


def test_admin_can_delete_user_but_not_bootstrap(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete", email="gone@example.com")
    r = client.delete(f"/api/admin/users/{target['public_id']}", headers=_bearer(admin_token))
    assert r.status_code == 204
    assert pdb.get_user_by_public_id(target["public_id"]) is None

    bootstrap = pdb.get_or_create_bootstrap_coach()
    b = client.delete(f"/api/admin/users/{bootstrap['public_id']}", headers=_bearer(admin_token))
    assert b.status_code == 403


def test_admin_actions_are_audited(client: TestClient):
    admin_token, admin_pid = _session("admin")
    target = pdb.create_user(role="athlete")
    client.post(
        f"/api/admin/users/{target['public_id']}/role",
        headers=_bearer(admin_token),
        json={"role": "coach"},
    )
    entries = pdb.list_admin_audit()
    assert any(
        e["action"] == "role_change" and e["target_public_id"] == target["public_id"]
        for e in entries
    )
    assert all(e["actor_public_id"] == admin_pid for e in entries)


def test_admin_audit_endpoint(client: TestClient):
    admin_token, admin_pid = _session("admin")
    target = pdb.create_user(role="athlete")
    client.post(
        f"/api/admin/users/{target['public_id']}/unlock",
        headers=_bearer(admin_token),
    )
    r = client.get("/api/admin/audit?limit=10", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    entry = r.json()[0]
    assert entry["action"] == "unlock_account"
    assert entry["actor_public_id"] == admin_pid
    assert entry["target_public_id"] == target["public_id"]


def test_admin_audit_filters_and_pagination(client: TestClient):
    admin_token, _ = _session("admin")
    target = pdb.create_user(role="athlete", email="target@example.com")
    client.post(f"/api/admin/users/{target['public_id']}/unlock", headers=_bearer(admin_token))
    client.post(
        f"/api/admin/users/{target['public_id']}/role",
        headers=_bearer(admin_token),
        json={"role": "coach"},
    )

    r = client.get("/api/admin/audit?action=unlock_account", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert [e["action"] for e in r.json()] == ["unlock_account"]

    r = client.get("/api/admin/audit?q=target@example.com", headers=_bearer(admin_token))
    assert {e["action"] for e in r.json()} == {"unlock_account", "role_change"}
    assert r.json()[0]["target_label"] == "target@example.com"
    assert r.json()[0]["actor_label"] is None  # admin sans nom ni email → fallback public_id

    r = client.get("/api/admin/audit?limit=1", headers=_bearer(admin_token))
    newest = r.json()[0]
    r = client.get(
        f"/api/admin/audit?limit=1&before_id={newest['id']}", headers=_bearer(admin_token)
    )
    assert r.json()[0]["id"] < newest["id"]

    assert (
        client.get("/api/admin/audit?period=nope", headers=_bearer(admin_token)).status_code == 422
    )


def test_audit_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    assert client.get("/api/admin/audit", headers=_bearer(athlete_token)).status_code == 403


# --- Invitations plateforme ---------------------------------------------------


def test_admin_lists_and_revokes_invitations(client: TestClient):
    admin_token, _ = _session("admin")
    coach = pdb.create_user(role="coach", email="coach@example.com")
    inv, _token = pdb.create_invitation(created_by=coach["id"], role="athlete")

    r = client.get("/api/admin/invitations", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    row = next(i for i in r.json() if i["id"] == inv["id"])
    assert row["role"] == "athlete" and row["status"] == "pending"
    assert row["created_by_email"] == "coach@example.com"

    d = client.delete(f"/api/admin/invitations/{inv['id']}", headers=_bearer(admin_token))
    assert d.status_code == 204
    assert pdb.list_invitations(created_by=None)[0]["status"] == "revoked"


def test_admin_revoke_unknown_invitation_is_404(client: TestClient):
    admin_token, _ = _session("admin")
    assert (
        client.delete("/api/admin/invitations/999999", headers=_bearer(admin_token)).status_code
        == 404
    )


def test_invitations_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    assert client.get("/api/admin/invitations", headers=_bearer(athlete_token)).status_code == 403


def test_admin_creates_invitation(client: TestClient):
    admin_token, admin_pid = _session("admin")
    r = client.post("/api/admin/invitations", headers=_bearer(admin_token), json={"role": "coach"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["invite_url"].startswith("/accept-invite?token=")
    assert body["invitation"]["role"] == "coach"
    assert body["invitation"]["status"] == "pending"
    assert body["invitation"]["created_by_public_id"] == admin_pid

    rows = client.get("/api/admin/invitations", headers=_bearer(admin_token)).json()
    assert any(i["id"] == body["invitation"]["id"] for i in rows)


def test_admin_created_invitation_accepts_with_signup_disabled(client: TestClient):
    """Le lien admin crée un compte même quand l'inscription publique est coupée."""
    pdb.set_setting("signup_enabled", "0")
    admin_token, _ = _session("admin")
    # Signup public désactivé → refusé.
    denied = client.post(
        "/api/auth/signup",
        json={"email": "x@example.com", "password": "secret123456", "role": "coach"},
    )
    assert denied.status_code == 403, denied.text

    r = client.post("/api/admin/invitations", headers=_bearer(admin_token), json={"role": "coach"})
    invite_token = r.json()["invite_url"].split("token=", 1)[1]
    accepted = client.post("/api/auth/accept-invite", json={"invite_token": invite_token})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "coach"


def test_admin_create_invitation_rejects_admin_role(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.post("/api/admin/invitations", headers=_bearer(admin_token), json={"role": "admin"})
    assert r.status_code == 422


def test_admin_create_invitation_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    r = client.post(
        "/api/admin/invitations", headers=_bearer(athlete_token), json={"role": "athlete"}
    )
    assert r.status_code == 403


# --- Réglages étendus + annonce -----------------------------------------------


def test_admin_settings_announcement(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.put(
        "/api/admin/settings",
        headers=_bearer(admin_token),
        json={"maintenance_mode": True, "broadcast_message": "  Maintenance prévue  "},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["maintenance_mode"] is True
    assert body["broadcast_message"] == "Maintenance prévue"

    # Le message vide retire le bandeau.
    r2 = client.put(
        "/api/admin/settings", headers=_bearer(admin_token), json={"broadcast_message": "   "}
    )
    assert r2.json()["broadcast_message"] is None


def test_admin_settings_rejects_long_message(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.put(
        "/api/admin/settings",
        headers=_bearer(admin_token),
        json={"broadcast_message": "x" * 501},
    )
    assert r.status_code == 422


def test_announcement_readable_by_any_account(client: TestClient):
    admin_token, _ = _session("admin")
    athlete_token, _ = _session("athlete")
    client.put(
        "/api/admin/settings",
        headers=_bearer(admin_token),
        json={"maintenance_mode": True, "broadcast_message": "Info"},
    )

    for token in (admin_token, athlete_token):
        r = client.get("/api/announcement", headers=_bearer(token))
        assert r.status_code == 200, r.text
        assert r.json() == {"maintenance_mode": True, "message": "Info"}


# --- Stats & statut ops -------------------------------------------------------


def test_admin_stats(client: TestClient):
    admin_token, _ = _session("admin")
    _session("athlete")
    r = client.get("/api/admin/stats", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["users_by_role"].get("admin", 0) >= 1
    assert body["users_by_role"].get("athlete", 0) >= 1
    assert isinstance(body["active_sessions"], int)
    assert body["platform_db_bytes"] > 0


def test_admin_status(client: TestClient):
    admin_token, _ = _session("admin")
    r = client.get("/api/admin/status", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["scheduler_running"], bool)
    assert isinstance(body["jobs"], list)


def test_stats_and_status_forbidden_for_non_admin(client: TestClient):
    athlete_token, _ = _session("athlete")
    assert client.get("/api/admin/stats", headers=_bearer(athlete_token)).status_code == 403
    assert client.get("/api/admin/status", headers=_bearer(athlete_token)).status_code == 403


def test_admin_purges_orphan_athlete_spaces(client: TestClient):
    from domestique_ai.config import get_athletes_root

    admin_token, _ = _session("admin")
    root = get_athletes_root()
    orphan = root / "deadbeefdeadbeefdeadbeefdeadbeef"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "strava_activities.db").write_text("")
    known = pdb.create_user(role="athlete")
    (root / known["public_id"]).mkdir(parents=True, exist_ok=True)

    stats = client.get("/api/admin/stats", headers=_bearer(admin_token)).json()
    assert stats["orphan_athlete_spaces"] == 1

    r = client.post("/api/admin/athlete-spaces/purge-orphans", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1
    assert not orphan.exists()
    assert (root / known["public_id"]).exists()
    assert any(e["action"] == "purge_orphan_spaces" for e in pdb.list_admin_audit())
