"""Tests de la vue coach roster (liste d'athlètes + impersonation lecture seule).

Monte les vrais routeurs avec l'auth activée. Le token legacy résout le coach
bootstrap : les athlètes invités via ce token lui sont rattachés (table
``coach_athlete``). On vérifie la liste des athlètes, l'impersonation autorisée
(``?athlete=<public_id>``), refusée (hors roster / non-coach / inexistant) et la
garantie lecture seule (toute écriture sur un athlète ciblé → 403).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import activities as activities_router
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import availability as availability_router
from domestique_ai.api.routers import coach as coach_router
from domestique_ai.api.routers import metrics as metrics_router
from domestique_ai.api.routers import morning as morning_router
from domestique_ai.api.routers import objective as objective_router
from domestique_ai.api.routers import plan as plan_router
from domestique_ai.api.routers import profile as profile_router
from domestique_ai.api.routers import strava as strava_router

_LEGACY = "legacy-roster-token"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    for mod in (
        metrics_router, activities_router, morning_router,
        objective_router, profile_router, availability_router,
        strava_router, coach_router, plan_router,
    ):
        app.include_router(mod.router)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def env(tmp_path: Path, monkeypatch) -> Iterator[dict]:
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    with TestClient(_make_app()) as c:
        yield {"client": c, "root": tmp_path / "athletes"}


def _new_athlete(client: TestClient) -> tuple[str, str]:
    """Invite (coach legacy) + accepte un athlète. Retourne (session_token, public_id)."""
    inv = client.post(
        "/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"}
    )
    assert inv.status_code == 200, inv.text
    acc = client.post(
        "/api/auth/accept-invite", json={"invite_token": inv.json()["invite_token"]}
    )
    assert acc.status_code == 200, acc.text
    session = acc.json()["session_token"]
    me = client.get("/api/auth/me", headers=_bearer(session))
    return session, me.json()["public_id"]


def _new_coach(client: TestClient) -> str:
    """Invite + accepte un second coach (sans athlètes). Retourne son session_token."""
    inv = client.post(
        "/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "coach"}
    )
    assert inv.status_code == 200, inv.text
    acc = client.post(
        "/api/auth/accept-invite", json={"invite_token": inv.json()["invite_token"]}
    )
    assert acc.status_code == 200, acc.text
    return acc.json()["session_token"]


def _seed_activity(root: Path, public_id: str, *, strava_id: int,
                   training_load: float, date_iso: str) -> None:
    db = root / public_id / "strava_activities.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration, training_load) "
            "VALUES (?, ?, ?, ?)",
            (strava_id, date_iso, 3600, training_load),
        )
        conn.commit()
    finally:
        conn.close()


# --- GET /api/auth/athletes ---------------------------------------------------

def test_list_athletes_returns_coach_roster(env):
    c, root = env["client"], env["root"]
    _, a_pid = _new_athlete(c)
    _, b_pid = _new_athlete(c)
    today = dt.date.today().isoformat()
    _seed_activity(root, a_pid, strava_id=1, training_load=100.0, date_iso=today)
    _seed_activity(root, a_pid, strava_id=2, training_load=50.0, date_iso=today)

    r = c.get("/api/auth/athletes", headers=_bearer(_LEGACY))
    assert r.status_code == 200, r.text
    by_pid = {a["public_id"]: a for a in r.json()}
    assert set(by_pid) == {a_pid, b_pid}
    assert by_pid[a_pid]["n_activities"] == 2
    assert by_pid[a_pid]["last_activity_date"] == today
    assert by_pid[a_pid]["strava_connected"] is False
    # B n'a aucune activité ni DB seedée → zéros.
    assert by_pid[b_pid]["n_activities"] == 0
    assert by_pid[b_pid]["last_activity_date"] is None


def test_list_athletes_forbidden_for_athlete(env):
    c = env["client"]
    a_sess, _ = _new_athlete(c)
    assert c.get("/api/auth/athletes", headers=_bearer(a_sess)).status_code == 403


# --- Impersonation autorisée --------------------------------------------------

def test_impersonation_returns_target_data(env):
    c, root = env["client"], env["root"]
    a_sess, a_pid = _new_athlete(c)
    b_sess, b_pid = _new_athlete(c)
    today = dt.date.today().isoformat()
    _seed_activity(root, a_pid, strava_id=1, training_load=100.0, date_iso=today)
    _seed_activity(root, b_pid, strava_id=1, training_load=40.0, date_iso=today)

    # Le coach (legacy) consulte A puis B : il voit bien LEURS données distinctes.
    coach_a = c.get(
        "/api/metrics/load", headers=_bearer(_LEGACY), params={"athlete": a_pid}
    ).json()
    coach_b = c.get(
        "/api/metrics/load", headers=_bearer(_LEGACY), params={"athlete": b_pid}
    ).json()
    own_a = c.get("/api/metrics/load", headers=_bearer(a_sess)).json()
    own_b = c.get("/api/metrics/load", headers=_bearer(b_sess)).json()

    assert coach_a["current"]["ctl"] == own_a["current"]["ctl"]
    assert coach_b["current"]["ctl"] == own_b["current"]["ctl"]
    assert coach_a["current"]["ctl"] != coach_b["current"]["ctl"]


# --- Impersonation refusée ----------------------------------------------------

def test_impersonation_out_of_roster_forbidden(env):
    c = env["client"]
    _, a_pid = _new_athlete(c)
    other_coach = _new_coach(c)  # n'a aucun athlète
    r = c.get(
        "/api/metrics/load", headers=_bearer(other_coach), params={"athlete": a_pid}
    )
    assert r.status_code == 403


def test_impersonation_unknown_public_id_forbidden(env):
    c = env["client"]
    r = c.get(
        "/api/metrics/load", headers=_bearer(_LEGACY), params={"athlete": "does-not-exist"}
    )
    assert r.status_code == 403


def test_impersonation_by_non_coach_forbidden(env):
    c = env["client"]
    a_sess, a_pid = _new_athlete(c)
    b_sess, _ = _new_athlete(c)
    # B (athlète) tente de consulter A → 403 (rôle insuffisant).
    r = c.get("/api/metrics/load", headers=_bearer(b_sess), params={"athlete": a_pid})
    assert r.status_code == 403


# --- Lecture seule ------------------------------------------------------------

def test_impersonation_is_read_only(env):
    c = env["client"]
    _, a_pid = _new_athlete(c)
    # POST et PUT sur un athlète ciblé → 403 (consultation lecture seule).
    assert c.post(
        "/api/morning", headers=_bearer(_LEGACY), params={"athlete": a_pid},
        json={"hrv_ms": 80},
    ).status_code == 403
    assert c.put(
        "/api/profile", headers=_bearer(_LEGACY), params={"athlete": a_pid},
        json={"ftp": 300, "hr_rest": None, "hr_max": None, "sex": "M", "lthr_pct": 0.88},
    ).status_code == 403
