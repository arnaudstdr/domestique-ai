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
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import roster as roster_router

_LEGACY = "legacy-token-1234"


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)
    app.include_router(auth_router.router)
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
def client_auth_off() -> Iterator[TestClient]:
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
    r2 = client.post("/api/auth/accept-invite", json={"invite_token": invite_token})
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
        client.post("/api/auth/accept-invite", json={"invite_token": invite_token}).status_code
        == 200
    )
    assert (
        client.post("/api/auth/accept-invite", json={"invite_token": invite_token}).status_code
        == 400
    )


def test_accept_unknown_invite_fails(client: TestClient) -> None:
    r = client.post("/api/auth/accept-invite", json={"invite_token": "nope"})
    assert r.status_code == 400


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
    acc = client.post("/api/auth/accept-invite", json={"invite_token": invite_token})
    assert acc.status_code == 400


def test_revoke_unknown_invitation_404(client: TestClient) -> None:
    assert client.delete("/api/auth/invitations/9999", headers=_bearer(_LEGACY)).status_code == 404


def test_revoke_already_accepted_invitation_404(client: TestClient) -> None:
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    invite_token = r.json()["invite_token"]
    client.post("/api/auth/accept-invite", json={"invite_token": invite_token})
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


# ---- Mode auth-off (dev) ----------------------------------------------------


def test_auth_off_me_is_bootstrap_coach(client_auth_off: TestClient) -> None:
    r = client_auth_off.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "coach"


def test_auth_off_data_passes(client_auth_off: TestClient) -> None:
    assert client_auth_off.get("/api/data").status_code == 200
