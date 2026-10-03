"""Tests d'intégration du formulaire de retour (``POST /api/feedback``).

Mini-app dédiée (comme ``tests/test_auth_api.py``) pour contrôler le token sans
dépendre de l'ordre d'import de ``domestique_ai.api.main``. La DB plateforme est
isolée par le conftest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import feedback as feedback_router
from domestique_ai.platform_db import list_feedback

_LEGACY = "legacy-token-1234"


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=token)
    app.include_router(auth_router.router)
    app.include_router(feedback_router.router)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(_make_app(_LEGACY)) as c:
        yield c


def _athlete_token(client: TestClient) -> str:
    """Crée une invitation coach et l'accepte pour obtenir une session athlète."""
    r = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    assert r.status_code == 200, r.text
    invite_token = r.json()["invite_token"]
    r2 = client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite_token,
            "accepts_terms": True,
            "accepts_health_data": True,
        },
    )
    assert r2.status_code == 200, r2.text
    return r2.json()["session_token"]


def test_submit_feedback_requires_auth(client: TestClient) -> None:
    assert client.post("/api/feedback", json={"category": "bug", "message": "x"}).status_code == 401
    assert (
        client.post(
            "/api/feedback",
            headers=_bearer("wrong"),
            json={"category": "bug", "message": "x"},
        ).status_code
        == 401
    )


def test_submit_feedback_persists_row(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={
            "category": "idea",
            "message": "  Une super idée  ",
            "page": "/activites",
            "app_version": "1.2.3",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] >= 1
    assert body["created_at"]

    rows = list_feedback()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["category"] == "idea"
    assert entry["message"] == "Une super idée"
    assert entry["page"] == "/activites"
    assert entry["app_version"] == "1.2.3"
    assert entry["role"] == "coach"
    assert entry["status"] == "new"


def test_submit_feedback_invalid_category(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={"category": "nope", "message": "x"},
    )
    assert r.status_code == 422


def test_submit_feedback_blank_message(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={"category": "bug", "message": "   "},
    )
    assert r.status_code == 422
    assert list_feedback() == []


def test_submit_feedback_notifies_configured_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict]] = []

    def _fake_send(to: str, feedback: dict) -> bool:
        calls.append((to, feedback))
        return True

    monkeypatch.setenv("DOMESTIQUE_AI_FEEDBACK_EMAIL", "retours@example.com")
    monkeypatch.setattr(feedback_router, "send_feedback_notification", _fake_send)

    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={"category": "remark", "message": "Top"},
    )
    assert r.status_code == 201, r.text
    assert len(calls) == 1
    assert calls[0][0] == "retours@example.com"
    assert calls[0][1]["category"] == "remark"


def test_submit_feedback_no_notify_when_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.delenv("DOMESTIQUE_AI_FEEDBACK_EMAIL", raising=False)
    monkeypatch.setattr(
        feedback_router, "send_feedback_notification", lambda to, fb: calls.append(to) or True
    )

    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={"category": "other", "message": "ok"},
    )
    assert r.status_code == 201, r.text
    assert calls == []


def test_submit_feedback_notify_failure_is_swallowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(to: str, feedback: dict) -> bool:
        raise RuntimeError("SMTP down")

    monkeypatch.setenv("DOMESTIQUE_AI_FEEDBACK_EMAIL", "retours@example.com")
    monkeypatch.setattr(feedback_router, "send_feedback_notification", _boom)

    r = client.post(
        "/api/feedback",
        headers=_bearer(_LEGACY),
        json={"category": "bug", "message": "ça casse"},
    )
    assert r.status_code == 201, r.text
    assert len(list_feedback()) == 1


def test_submit_feedback_rate_limited(client: TestClient) -> None:
    payload = {"category": "other", "message": "spam"}
    for _ in range(feedback_router._MAX_PER_HOUR):
        assert (
            client.post("/api/feedback", headers=_bearer(_LEGACY), json=payload).status_code == 201
        )
    r = client.post("/api/feedback", headers=_bearer(_LEGACY), json=payload)
    assert r.status_code == 429


def test_feedback_records_athlete_identity(client: TestClient) -> None:
    token = _athlete_token(client)
    r = client.post(
        "/api/feedback",
        headers=_bearer(token),
        json={"category": "bug", "message": "bug athlète"},
    )
    assert r.status_code == 201, r.text
    entry = list_feedback()[0]
    assert entry["role"] == "athlete"
    assert entry["public_id"]
    assert entry["user_id"] is not None
