"""Tests d'intégration du login mot de passe + 2FA TOTP (lots 3-4).

Mini-app dédiée (comme test_auth_api.py). DB plateforme isolée par le conftest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pyotp
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.deps import get_current_user, require_coach
from domestique_ai.api.routers import auth as auth_router

_LEGACY = "legacy-token-1234"


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)
    app.include_router(auth_router.router)

    @app.get("/api/data", dependencies=[Depends(require_coach)])  # noqa: B008
    def _data() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/self")
    def _self(user: dict = Depends(get_current_user)) -> dict[str, str]:  # noqa: B008
        return {"public_id": user["public_id"]}

    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(_make_app(_LEGACY)) as c:
        yield c


def _make_athlete_account(
    client: TestClient,
    email: str = "alice@example.com",
    password: str = "supersecret1",
) -> str:
    """Coach invite + accepte avec email/mot de passe. Retourne le session token."""
    r = client.post(
        "/api/auth/invitations",
        headers=_bearer(_LEGACY),
        json={"role": "athlete"},
    )
    invite_token = r.json()["invite_token"]
    r2 = client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite_token,
            "display_name": "Alice",
            "email": email,
            "password": password,
        },
    )
    assert r2.status_code == 200, r2.text
    return r2.json()["session_token"]


def _enroll_totp(client: TestClient, session: str) -> str:
    """Enrôle la 2FA pour une session. Retourne le secret TOTP."""
    enroll = client.post("/api/auth/totp/enroll", headers=_bearer(session))
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify = client.post("/api/auth/totp/verify", headers=_bearer(session), json={"code": code})
    assert verify.status_code == 200, verify.text
    return secret


# ---- Login mot de passe -------------------------------------------------------


def test_login_unknown_email_is_generic_401(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Email ou mot de passe incorrect."


def test_login_wrong_password_then_lockout(client: TestClient) -> None:
    _make_athlete_account(client)
    for _ in range(5):
        r = client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "wrong"},
        )
        assert r.status_code == 401
    # Compte verrouillé après le seuil.
    r = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "supersecret1"}
    )
    assert r.status_code == 429


def test_login_returns_session_when_totp_not_enrolled(client: TestClient) -> None:
    session = _make_athlete_account(client)
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["session_token"]
    # La session historique (avant re-login) reste utilisable sur les routes autorisées.
    assert client.get("/api/auth/me", headers=_bearer(session)).status_code == 200


# ---- 2FA obligatoire : blocage puis enrôlement --------------------------------


def test_session_with_password_but_no_totp_is_blocked(client: TestClient) -> None:
    session = _make_athlete_account(client)
    # has_password + totp_enabled=0 → bloqué hors routes d'enrôlement.
    r = client.get("/api/self", headers=_bearer(session))
    assert r.status_code == 403
    assert r.json()["detail"] == "totp_setup_required"
    # ... mais /me reste accessible.
    assert client.get("/api/auth/me", headers=_bearer(session)).status_code == 200


def test_full_totp_enrollment_and_login_flow(client: TestClient) -> None:
    session = _make_athlete_account(client)

    enroll = client.post("/api/auth/totp/enroll", headers=_bearer(session))
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    assert enroll.json()["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")

    code = pyotp.TOTP(secret).now()
    verify = client.post("/api/auth/totp/verify", headers=_bearer(session), json={"code": code})
    assert verify.status_code == 200, verify.text
    recovery_codes = verify.json()["recovery_codes"]
    assert len(recovery_codes) == 10

    # Une fois la 2FA active, la session passe la garde.
    assert client.get("/api/self", headers=_bearer(session)).status_code == 200

    # Nouveau login → challenge 2FA, puis code TOTP.
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "totp_required"
    challenge = r.json()["challenge"]
    assert not r.json()["session_token"]

    bad = client.post("/api/auth/login/totp", json={"challenge": challenge, "code": "000000"})
    assert bad.status_code == 401

    good = client.post(
        "/api/auth/login/totp",
        json={"challenge": challenge, "code": pyotp.TOTP(secret).now()},
    )
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "ok"
    assert good.json()["session_token"]


def test_login_totp_accepts_recovery_code(client: TestClient) -> None:
    session = _make_athlete_account(client, email="bob@example.com")
    enroll = client.post("/api/auth/totp/enroll", headers=_bearer(session)).json()
    code = pyotp.TOTP(enroll["secret"]).now()
    recovery = client.post(
        "/api/auth/totp/verify", headers=_bearer(session), json={"code": code}
    ).json()["recovery_codes"]

    challenge = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "supersecret1"},
    ).json()["challenge"]
    r = client.post("/api/auth/login/totp", json={"challenge": challenge, "code": recovery[0]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # Le code de secours est à usage unique : réutilisé → 401.
    challenge2 = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "supersecret1"},
    ).json()["challenge"]
    r2 = client.post("/api/auth/login/totp", json={"challenge": challenge2, "code": recovery[0]})
    assert r2.status_code == 401


def test_login_totp_rejects_bad_challenge(client: TestClient) -> None:
    r = client.post("/api/auth/login/totp", json={"challenge": "garbage", "code": "123456"})
    assert r.status_code == 401


# ---- accept-invite : validations ---------------------------------------------


def test_accept_invite_weak_password_rejected(client: TestClient) -> None:
    invite = client.post(
        "/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"}
    ).json()["invite_token"]
    r = client.post(
        "/api/auth/accept-invite",
        json={"invite_token": invite, "email": "weak@example.com", "password": "short"},
    )
    assert r.status_code == 422


def test_accept_invite_duplicate_email_conflict(client: TestClient) -> None:
    _make_athlete_account(client, email="dup@example.com")
    invite = client.post(
        "/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"}
    ).json()["invite_token"]
    r = client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite,
            "email": "DUP@example.com",
            "password": "supersecret2",
        },
    )
    assert r.status_code == 409
    # L'invitation reste consommable (transaction annulée).
    r2 = client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite,
            "email": "other@example.com",
            "password": "supersecret2",
        },
    )
    assert r2.status_code == 200, r2.text


# ---- Changement de mot de passe / codes de secours / désactivation -----------


def test_change_password_and_relogin(client: TestClient) -> None:
    session = _make_athlete_account(client)
    _enroll_totp(client, session)
    r = client.post(
        "/api/auth/password",
        headers=_bearer(session),
        json={"current_password": "supersecret1", "new_password": "brandnewpass9"},
    )
    assert r.status_code == 200
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "supersecret1"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "brandnewpass9"},
        ).status_code
        == 200
    )


def test_change_password_wrong_current_rejected(client: TestClient) -> None:
    session = _make_athlete_account(client)
    _enroll_totp(client, session)
    r = client.post(
        "/api/auth/password",
        headers=_bearer(session),
        json={"current_password": "nope", "new_password": "brandnewpass9"},
    )
    assert r.status_code == 401


def test_totp_disable_requires_password(client: TestClient) -> None:
    session = _make_athlete_account(client)
    enroll = client.post("/api/auth/totp/enroll", headers=_bearer(session)).json()
    code = pyotp.TOTP(enroll["secret"]).now()
    client.post("/api/auth/totp/verify", headers=_bearer(session), json={"code": code})

    r = client.post("/api/auth/totp/disable", headers=_bearer(session), json={"password": "wrong"})
    assert r.status_code == 401

    r2 = client.post(
        "/api/auth/totp/disable",
        headers=_bearer(session),
        json={"password": "supersecret1"},
    )
    assert r2.status_code == 200
    # Sans 2FA + identifiants, la session rebascule en mode enrôlement obligatoire.
    assert client.get("/api/self", headers=_bearer(session)).status_code == 403


# ---- Bootstrap / legacy -------------------------------------------------------


def test_setup_credentials_on_legacy_session_then_enforced(client: TestClient) -> None:
    # Le coach bootstrap (token legacy) n'a pas d'identifiants : il n'est pas bloqué.
    assert client.get("/api/self", headers=_bearer(_LEGACY)).status_code == 200
    r = client.post(
        "/api/auth/setup-credentials",
        headers=_bearer(_LEGACY),
        json={"email": "coach@example.com", "password": "coachsecret1"},
    )
    assert r.status_code == 200, r.text
    # Une fois les identifiants posés, le bootstrap reste break-glass (non bloqué).
    assert client.get("/api/self", headers=_bearer(_LEGACY)).status_code == 200
    # Mais l'email est pris : un second appel de setup échoue.
    r2 = client.post(
        "/api/auth/setup-credentials",
        headers=_bearer(_LEGACY),
        json={"email": "coach2@example.com", "password": "coachsecret1"},
    )
    assert r2.status_code == 409
