"""Tests d'intégration du socle identité (palier 1a) : middleware + routeur auth.

Mini-app dédiée (comme tests/test_auth.py) pour contrôler le token sans dépendre
de l'ordre d'import de `domestique_ai.api.main`. La DB plateforme est isolée par
le conftest (env + init).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.deps import require_coach
from domestique_ai.api.routers import account as account_router
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import roster as roster_router

_LEGACY = "legacy-token-1234"

# Consentements requis par les flux self-service (signup + accept-invite).
_CONSENTS = {"accepts_terms": True, "accepts_health_data": True}


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)
    app.include_router(auth_router.router)
    app.include_router(account_router.router)
    app.include_router(roster_router.router)

    @app.get("/api/data", dependencies=[Depends(require_coach)])  # noqa: B008
    def _data() -> dict[str, bool]:
        return {"ok": True}

    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(_make_app(_LEGACY)) as c:
        yield c


@pytest.fixture()
def client_auth_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Le mode auth-off doit vraiment être dépourvu de token : sinon le .env du
    # dev (DOMESTIQUE_AI_API_TOKEN renseigné) fait tomber get_current_user sur
    # un 401 au lieu du fallback bootstrap coach.
    monkeypatch.delenv("DOMESTIQUE_AI_API_TOKEN", raising=False)
    with TestClient(_make_app(None)) as c:
        yield c


# ---- Legacy token → coach bootstrap -----------------------------------------


def test_me_with_legacy_token_is_bootstrap_coach(client: TestClient) -> None:
    r = client.get("/api/auth/me", headers=_bearer(_LEGACY))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "coach"
    assert body["public_id"]


def test_data_requires_token(client: TestClient) -> None:
    assert client.get("/api/data").status_code == 401
    assert client.get("/api/data", headers=_bearer("wrong")).status_code == 401
    assert client.get("/api/data", headers=_bearer(_LEGACY)).status_code == 200


# ---- Flux invitation → acceptation → session athlète ------------------------


def _invite_and_accept(client: TestClient, role: str = "athlete") -> str:
    """Crée une invitation (coach) et l'accepte. Retourne le session token athlète."""
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": role})
    assert r.status_code == 200, r.text
    invite_token = r.json()["invite_token"]

    # accept-invite est public (exempté du Bearer).
    r2 = client.post("/api/auth/accept-invite", json={"invite_token": invite_token, **_CONSENTS})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["role"] == role
    return body["session_token"]


def test_invitation_flow_creates_usable_athlete_session(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    me = client.get("/api/auth/me", headers=_bearer(session_token))
    assert me.status_code == 200
    assert me.json()["role"] == "athlete"


def test_athlete_is_blocked_on_data_and_invitations(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    # Gating coach-only sur les données.
    assert client.get("/api/data", headers=_bearer(session_token)).status_code == 403
    # Et sur la création d'invitations.
    r = client.post(
        "/api/auth/invitations", headers=_bearer(session_token), json={"role": "athlete"}
    )
    assert r.status_code == 403


def test_accept_invite_twice_fails(client: TestClient) -> None:
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    invite_token = r.json()["invite_token"]
    assert (
        client.post(
            "/api/auth/accept-invite", json={"invite_token": invite_token, **_CONSENTS}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/accept-invite", json={"invite_token": invite_token, **_CONSENTS}
        ).status_code
        == 400
    )


def test_accept_unknown_invite_fails(client: TestClient) -> None:
    r = client.post("/api/auth/accept-invite", json={"invite_token": "nope", **_CONSENTS})
    assert r.status_code == 400


def test_accept_invite_requires_consents(client: TestClient) -> None:
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    invite_token = r.json()["invite_token"]
    assert (
        client.post("/api/auth/accept-invite", json={"invite_token": invite_token}).status_code
        == 422
    )
    # Seulement les CGU ne suffisent pas : le consentement santé est explicite.
    assert (
        client.post(
            "/api/auth/accept-invite",
            json={"invite_token": invite_token, "accepts_terms": True},
        ).status_code
        == 422
    )


# ---- Révocation d'invitation ------------------------------------------------


def test_revoke_pending_invitation(client: TestClient) -> None:
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    invite_token = r.json()["invite_token"]
    listed = client.get("/api/auth/invitations", headers=_bearer(_LEGACY)).json()
    inv_id = listed[0]["id"]

    # Révocation par le coach créateur.
    d = client.delete(f"/api/auth/invitations/{inv_id}", headers=_bearer(_LEGACY))
    assert d.status_code == 204
    # Le statut passe à revoked.
    after = client.get("/api/auth/invitations", headers=_bearer(_LEGACY)).json()
    assert after[0]["status"] == "revoked"
    # Le lien révoqué n'est plus acceptable.
    acc = client.post("/api/auth/accept-invite", json={"invite_token": invite_token, **_CONSENTS})
    assert acc.status_code == 400


def test_revoke_unknown_invitation_404(client: TestClient) -> None:
    assert client.delete("/api/auth/invitations/9999", headers=_bearer(_LEGACY)).status_code == 404


def test_revoke_already_accepted_invitation_404(client: TestClient) -> None:
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    invite_token = r.json()["invite_token"]
    client.post("/api/auth/accept-invite", json={"invite_token": invite_token, **_CONSENTS})
    inv_id = client.get("/api/auth/invitations", headers=_bearer(_LEGACY)).json()[0]["id"]
    # Une invitation déjà acceptée n'est pas révocable (statut != pending).
    assert (
        client.delete(f"/api/auth/invitations/{inv_id}", headers=_bearer(_LEGACY)).status_code
        == 404
    )


def test_athlete_cannot_revoke_invitation(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    assert (
        client.delete("/api/auth/invitations/1", headers=_bearer(session_token)).status_code == 403
    )


# ---- Logout révoque la session ----------------------------------------------


def test_logout_revokes_session(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    assert client.get("/api/auth/me", headers=_bearer(session_token)).status_code == 200
    out = client.post("/api/auth/logout", headers=_bearer(session_token))
    assert out.status_code == 200
    # Token révoqué → 401.
    assert client.get("/api/auth/me", headers=_bearer(session_token)).status_code == 401


# ---- Reconnexion (athlète déconnecté → nouvelle session) --------------------


def test_reconnect_flow_after_logout(client: TestClient) -> None:
    # Un athlète accepte une invitation puis se déconnecte (session révoquée).
    session_token = _invite_and_accept(client, role="athlete")
    pid = client.get("/api/auth/me", headers=_bearer(session_token)).json()["public_id"]
    client.post("/api/auth/logout", headers=_bearer(session_token))
    assert client.get("/api/auth/me", headers=_bearer(session_token)).status_code == 401

    # Le coach génère un lien de reconnexion pour cet athlète.
    link = client.post(f"/api/roster/athletes/{pid}/reconnect-link", headers=_bearer(_LEGACY))
    assert link.status_code == 201, link.text
    url = link.json()["reconnect_url"]
    token = url.split("token=", 1)[1]

    # L'athlète échange le token (public) contre une NOUVELLE session valide.
    rec = client.post("/api/auth/reconnect", json={"token": token})
    assert rec.status_code == 200, rec.text
    new_session = rec.json()["session_token"]
    assert rec.json()["public_id"] == pid
    me = client.get("/api/auth/me", headers=_bearer(new_session))
    assert me.status_code == 200 and me.json()["public_id"] == pid


def test_reconnect_token_is_single_use(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    pid = client.get("/api/auth/me", headers=_bearer(session_token)).json()["public_id"]
    url = client.post(
        f"/api/roster/athletes/{pid}/reconnect-link", headers=_bearer(_LEGACY)
    ).json()["reconnect_url"]
    token = url.split("token=", 1)[1]
    assert client.post("/api/auth/reconnect", json={"token": token}).status_code == 200
    # 2e usage refusé.
    assert client.post("/api/auth/reconnect", json={"token": token}).status_code == 400


def test_reconnect_unknown_token_fails(client: TestClient) -> None:
    assert client.post("/api/auth/reconnect", json={"token": "nope"}).status_code == 400


def test_reconnect_link_forbidden_for_athlete(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    pid = client.get("/api/auth/me", headers=_bearer(session_token)).json()["public_id"]
    # Un athlète ne peut pas générer de lien de reconnexion.
    r = client.post(f"/api/roster/athletes/{pid}/reconnect-link", headers=_bearer(session_token))
    assert r.status_code == 403


# ---- Photo de profil --------------------------------------------------------

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def test_avatar_upload_and_clear(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    # Pas de photo au départ.
    assert client.get("/api/auth/me", headers=_bearer(session_token)).json()["avatar_url"] is None

    up = client.put(
        "/api/auth/me/avatar",
        headers=_bearer(session_token),
        files={"file": ("a.jpg", _JPEG, "image/jpeg")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["avatar_url"].startswith("data:image/jpeg;base64,")
    assert (
        client.get("/api/auth/me", headers=_bearer(session_token)).json()["avatar_url"]
        == up.json()["avatar_url"]
    )

    d = client.delete("/api/auth/me/avatar", headers=_bearer(session_token))
    assert d.status_code == 204
    assert client.get("/api/auth/me", headers=_bearer(session_token)).json()["avatar_url"] is None


def test_avatar_rejects_non_image(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    r = client.put(
        "/api/auth/me/avatar",
        headers=_bearer(session_token),
        files={"file": ("x.txt", b"not an image", "text/plain")},
    )
    assert r.status_code == 422


def test_avatar_rejects_oversize(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (500 * 1024)
    r = client.put(
        "/api/auth/me/avatar",
        headers=_bearer(session_token),
        files={"file": ("a.jpg", big, "image/jpeg")},
    )
    assert r.status_code == 422


def test_avatar_requires_auth(client: TestClient) -> None:
    assert (
        client.put(
            "/api/auth/me/avatar", files={"file": ("a.jpg", _JPEG, "image/jpeg")}
        ).status_code
        == 401
    )


# ---- Mode auth-off (dev) ----------------------------------------------------


def test_auth_off_me_is_bootstrap_coach(client_auth_off: TestClient) -> None:
    r = client_auth_off.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "coach"


def test_auth_off_data_passes(client_auth_off: TestClient) -> None:
    assert client_auth_off.get("/api/data").status_code == 200


# ---- Inscription publique self-service --------------------------------------

_STRONG_PASSWORD = "motdepasse1"


def _signup(client: TestClient, *, role: str, email: str, password: str = _STRONG_PASSWORD):
    return client.post(
        "/api/auth/signup",
        json={"email": email, "password": password, "role": role, **_CONSENTS},
    )


def _enable_totp(public_id: str) -> None:
    """Active la 2FA du compte (le portail TOTP exige un compte conforme)."""
    from domestique_ai.platform_db import enable_totp, get_user_by_public_id, set_totp_secret

    user = get_user_by_public_id(public_id)
    assert user is not None
    set_totp_secret(user["id"], "JBSWY3DPEHPK3PXP")
    assert enable_totp(user["id"])


def test_signup_disabled_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOMESTIQUE_AI_SIGNUP_ENABLED", raising=False)
    r = _signup(client, role="athlete", email="a@b.c")
    assert r.status_code == 403


def test_signup_athlete_creates_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    r = _signup(client, role="athlete", email="Ath@B.c")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "athlete"
    assert body["invite_url"] is None
    me = client.get("/api/auth/me", headers=_bearer(body["session_token"]))
    assert me.status_code == 200
    assert me.json()["email_verified"] is False
    assert me.json()["email"] == "ath@b.c"


def test_signup_coach_gets_reusable_invite_link(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    r = _signup(client, role="coach", email="coach@b.c")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invite_url"].startswith("/accept-invite?coach=")
    code = body["invite_url"].split("coach=", 1)[1]

    # Le coach peut relire son code (self-only).
    _enable_totp(body["public_id"])
    link = client.get("/api/auth/coach-invite-link", headers=_bearer(body["session_token"]))
    assert link.status_code == 200
    assert link.json()["coach_code"] == code

    # Rotation : l'ancien code meurt.
    rot = client.post("/api/auth/coach-invite-link/rotate", headers=_bearer(body["session_token"]))
    assert rot.status_code == 200
    assert rot.json()["coach_code"] != code


def test_signup_rejects_weak_password(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    r = _signup(client, role="athlete", email="a@b.c", password="court")
    assert r.status_code == 422


def test_signup_duplicate_email(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    assert _signup(client, role="athlete", email="dup@b.c").status_code == 200
    assert _signup(client, role="athlete", email="dup@b.c").status_code == 409


def test_invited_accounts_are_email_verified(client: TestClient) -> None:
    session_token = _invite_and_accept(client, role="athlete")
    me = client.get("/api/auth/me", headers=_bearer(session_token))
    assert me.json()["email_verified"] is True


# ---- Vérification d'email ---------------------------------------------------


def test_verify_email_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        auth_router.mailer,
        "send_verification_email",
        lambda to, token: captured.update(to=to, token=token) or True,
    )
    body = _signup(client, role="athlete", email="a@b.c").json()
    assert captured["token"]

    v = client.post("/api/auth/verify-email", json={"token": captured["token"]})
    assert v.status_code == 200
    me = client.get("/api/auth/me", headers=_bearer(body["session_token"]))
    assert me.json()["email_verified"] is True
    # Usage unique.
    assert (
        client.post("/api/auth/verify-email", json={"token": captured["token"]}).status_code == 400
    )


def test_resend_verification(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    called: list[str] = []
    monkeypatch.setattr(
        auth_router.mailer,
        "send_verification_email",
        lambda to, token: (called.append(token), True)[1],
    )
    body = _signup(client, role="athlete", email="a@b.c").json()
    assert len(called) == 1  # envoi à l'inscription
    r = client.post("/api/auth/resend-verification", headers=_bearer(body["session_token"]))
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert len(called) == 2


# ---- Mot de passe oublié ----------------------------------------------------


def test_forgot_password_is_anti_enumeration(client: TestClient) -> None:
    r = client.post("/api/auth/forgot-password", json={"email": "inconnu@b.c"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_reset_password_flow_revokes_sessions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        auth_router.mailer,
        "send_password_reset_email",
        lambda to, token: captured.update(token=token) or True,
    )
    body = _signup(client, role="athlete", email="a@b.c").json()
    old_session = body["session_token"]

    assert client.post("/api/auth/forgot-password", json={"email": "a@b.c"}).status_code == 200
    assert captured["token"]

    reset = client.post(
        "/api/auth/reset-password",
        json={"token": captured["token"], "new_password": "nouveaumdp1"},
    )
    assert reset.status_code == 200, reset.text
    # Toutes les sessions existantes sont révoquées.
    assert client.get("/api/auth/me", headers=_bearer(old_session)).status_code == 401
    # Le nouveau mot de passe fonctionne.
    login = client.post("/api/auth/login", json={"email": "a@b.c", "password": "nouveaumdp1"})
    assert login.status_code == 200
    assert login.json()["status"] == "ok"
    # Le token de reset est à usage unique.
    assert (
        client.post(
            "/api/auth/reset-password",
            json={"token": captured["token"], "new_password": "autremdp123"},
        ).status_code
        == 400
    )


# ---- Lien coach : création par code + rattachement d'un athlète existant ----


def _signup_coach(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, email: str
) -> tuple[str, str, str]:
    """Crée un coach, active sa 2FA, retourne (session, public_id, coach_code)."""
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="coach", email=email).json()
    _enable_totp(body["public_id"])
    code = body["invite_url"].split("coach=", 1)[1]
    return body["session_token"], body["public_id"], code


def test_accept_invite_by_coach_code_creates_athlete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    coach_session, _coach_pid, code = _signup_coach(client, monkeypatch, "coach@b.c")
    r = client.post(
        "/api/auth/accept-invite",
        json={"coach_code": code, "email": "ath@b.c", "password": _STRONG_PASSWORD, **_CONSENTS},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "athlete"
    roster = client.get("/api/auth/athletes", headers=_bearer(coach_session))
    assert roster.status_code == 200
    assert any(a["public_id"] == r.json()["public_id"] for a in roster.json())


def test_link_existing_athlete_does_not_create_duplicate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domestique_ai.platform_db import list_users

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    athlete = _signup(client, role="athlete", email="ath@b.c").json()
    _enable_totp(athlete["public_id"])
    coach_session, _coach_pid, code = _signup_coach(client, monkeypatch, "coach@b.c")

    before = len(list_users())
    link = client.post(
        "/api/auth/accept-invite/link",
        json={"coach_code": code},
        headers=_bearer(athlete["session_token"]),
    )
    assert link.status_code == 200, link.text
    assert link.json()["status"] == "linked"
    # Aucun compte créé.
    assert len(list_users()) == before
    roster = client.get("/api/auth/athletes", headers=_bearer(coach_session)).json()
    assert any(a["public_id"] == athlete["public_id"] for a in roster)


def test_link_rejected_for_coach_role(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    coach_session, _coach_pid, code = _signup_coach(client, monkeypatch, "coach@b.c")
    r = client.post(
        "/api/auth/accept-invite/link",
        json={"coach_code": code},
        headers=_bearer(coach_session),
    )
    assert r.status_code == 403


# ---- Suppression de son propre compte ---------------------------------------


def test_delete_own_account_removes_user_and_revokes_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domestique_ai.platform_db import get_user_by_email

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    session = body["session_token"]

    r = client.request(
        "DELETE", "/api/auth/me", json={"password": _STRONG_PASSWORD}, headers=_bearer(session)
    )
    assert r.status_code == 204, r.text
    # Compte effacé et session révoquée.
    assert get_user_by_email("a@b.c") is None
    assert client.get("/api/auth/me", headers=_bearer(session)).status_code == 401


def test_delete_own_account_wrong_password_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domestique_ai.platform_db import get_user_by_email

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    r = client.request(
        "DELETE",
        "/api/auth/me",
        json={"password": "mauvais-mot-de-passe"},
        headers=_bearer(body["session_token"]),
    )
    assert r.status_code == 401
    assert get_user_by_email("a@b.c") is not None


def test_delete_own_account_requires_totp_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    _enable_totp(body["public_id"])

    # Sans code → refusé.
    r = client.request(
        "DELETE",
        "/api/auth/me",
        json={"password": _STRONG_PASSWORD},
        headers=_bearer(body["session_token"]),
    )
    assert r.status_code == 401

    # Code TOTP valide → suppression.
    import pyotp

    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    r2 = client.request(
        "DELETE",
        "/api/auth/me",
        json={"password": _STRONG_PASSWORD, "code": code},
        headers=_bearer(body["session_token"]),
    )
    assert r2.status_code == 204, r2.text


def test_delete_account_protects_bootstrap(client: TestClient) -> None:
    r = client.request("DELETE", "/api/auth/me", json={}, headers=_bearer(_LEGACY))
    assert r.status_code == 403


def test_delete_own_account_removes_athlete_data_dir(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.platform_db import get_user_by_public_id

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    user = get_user_by_public_id(body["public_id"])
    assert user is not None
    db_path = context_for_athlete(user).db_path
    assert db_path.parent.exists()

    r = client.request(
        "DELETE",
        "/api/auth/me",
        json={"password": _STRONG_PASSWORD},
        headers=_bearer(body["session_token"]),
    )
    assert r.status_code == 204, r.text
    assert not db_path.parent.exists()


def test_totp_reenroll_requires_reauth_when_2fa_active(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remplacer une 2FA active exige la ré-auth (vuln-0001) : un vol de session ne suffit pas."""
    import pyotp

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    _enable_totp(body["public_id"])
    headers = _bearer(body["session_token"])

    # Sans ré-auth → refusé.
    assert client.post("/api/auth/totp/enroll", headers=headers).status_code == 403
    # Mauvais mot de passe → refusé.
    r = client.post(
        "/api/auth/totp/enroll",
        json={"password": "mauvais-mot-de-passe", "code": "abcdef"},
        headers=headers,
    )
    assert r.status_code == 401
    # Bon mot de passe mais code invalide → refusé.
    r = client.post(
        "/api/auth/totp/enroll",
        json={"password": _STRONG_PASSWORD, "code": "abcdef"},
        headers=headers,
    )
    assert r.status_code == 403
    # Ré-auth complète → nouveau secret.
    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    r = client.post(
        "/api/auth/totp/enroll",
        json={"password": _STRONG_PASSWORD, "code": code},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["secret"]


# ---- Consentements (CGU + données de santé) ---------------------------------


def test_signup_requires_consents(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    r = client.post("/api/auth/signup", json={"email": "a@b.c", "password": _STRONG_PASSWORD})
    assert r.status_code == 422
    r2 = client.post(
        "/api/auth/signup",
        json={
            "email": "a@b.c",
            "password": _STRONG_PASSWORD,
            "accepts_terms": True,
            "accepts_health_data": False,
        },
    )
    assert r2.status_code == 422


def test_signup_records_consent_versions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    version = client.get("/api/auth/config").json()["legal_version"]
    assert version
    me = client.get("/api/auth/me", headers=_bearer(body["session_token"])).json()
    assert me["terms_accepted_at"] and me["terms_accepted_version"] == version
    assert me["health_consent_at"] and me["health_consent_version"] == version
    assert me["health_consent_withdrawn_at"] is None


def test_reconsent_existing_account(client: TestClient) -> None:
    # Le compte propriétaire (legacy) n'a aucun consentement enregistré.
    me = client.get("/api/auth/me", headers=_bearer(_LEGACY)).json()
    assert me["terms_accepted_at"] is None
    # Consentement partiel refusé.
    partial = client.post(
        "/api/auth/me/consents",
        json={"accepts_terms": True, "accepts_health_data": False},
        headers=_bearer(_LEGACY),
    )
    assert partial.status_code == 422
    r = client.post(
        "/api/auth/me/consents",
        json={"accepts_terms": True, "accepts_health_data": True},
        headers=_bearer(_LEGACY),
    )
    assert r.status_code == 200, r.text
    assert r.json()["terms_accepted_at"] is not None
    assert r.json()["health_consent_at"] is not None


def test_withdraw_health_consent(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    headers = _bearer(body["session_token"])
    r = client.delete("/api/auth/me/consents/health", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["health_consent_withdrawn_at"] is not None
    # L'historique du consentement initial est conservé.
    assert r.json()["health_consent_at"] is not None


# ---- Export RGPD (portabilité) ----------------------------------------------


def test_export_account_returns_zip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import json
    import zipfile

    monkeypatch.setenv("DOMESTIQUE_AI_SIGNUP_ENABLED", "1")
    body = _signup(client, role="athlete", email="a@b.c").json()
    # L'export contient des données de santé : 2FA requise (middleware).
    _enable_totp(body["public_id"])
    r = client.get("/api/auth/me/export", headers=_bearer(body["session_token"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = set(archive.namelist())
        assert "account.json" in names
        assert "LISEZ-MOI.txt" in names
        account = json.loads(archive.read("account.json"))
        assert account["email"] == "a@b.c"
        assert account["consents"]["terms_accepted_at"] is not None
        # Aucun secret ne doit fuiter.
        assert "password_hash" not in account and "totp_secret" not in account


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/api/auth/me/export").status_code == 401
