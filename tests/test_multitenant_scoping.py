"""Tests d'isolation multi-tenant (palier 1b-i).

Monte les vrais routeurs scopés avec l'auth activée et vérifie que deux athlètes
distincts ne voient que LEURS données, que le profil est par athlète, et qu'il
n'y a pas de fuite de la config env vers un athlète non-propriétaire.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.deps import require_coach
from domestique_ai.api.routers import activities as activities_router
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import availability as availability_router
from domestique_ai.api.routers import coach as coach_router
from domestique_ai.api.routers import metrics as metrics_router
from domestique_ai.api.routers import morning as morning_router
from domestique_ai.api.routers import objective as objective_router
from domestique_ai.api.routers import profile as profile_router
from domestique_ai.api.routers import strava as strava_router

_LEGACY = "legacy-mt-token"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    for mod in (
        metrics_router, activities_router, morning_router,
        objective_router, profile_router, availability_router,
        strava_router,
    ):
        app.include_router(mod.router)
    # coach reste gaté coach-only (comme dans main.py) → test du 403 athlète.
    app.include_router(coach_router.router, dependencies=[Depends(require_coach)])
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


def _seed_activity(root: Path, public_id: str, *, strava_id: int,
                   training_load: float, date_iso: str) -> None:
    """Insère une activité dans la DB (déjà provisionnée) de l'athlète."""
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


def test_metrics_load_is_isolated_per_athlete(env):
    c, root = env["client"], env["root"]
    a_sess, a_pid = _new_athlete(c)
    b_sess, b_pid = _new_athlete(c)
    today = dt.date.today().isoformat()
    _seed_activity(root, a_pid, strava_id=1, training_load=100.0, date_iso=today)
    _seed_activity(root, b_pid, strava_id=1, training_load=40.0, date_iso=today)

    la = c.get("/api/metrics/load", headers=_bearer(a_sess)).json()
    lb = c.get("/api/metrics/load", headers=_bearer(b_sess)).json()
    assert la["current"] is not None and lb["current"] is not None
    assert la["current"]["ctl"] != lb["current"]["ctl"]


def test_morning_is_isolated_per_athlete(env):
    c = env["client"]
    a_sess, _ = _new_athlete(c)
    b_sess, _ = _new_athlete(c)
    assert c.post("/api/morning", headers=_bearer(a_sess), json={"hrv_ms": 80}).status_code == 204
    assert c.post("/api/morning", headers=_bearer(b_sess), json={"hrv_ms": 40}).status_code == 204

    a_hrv = [e["hrv_ms"] for e in c.get("/api/morning", headers=_bearer(a_sess)).json()["history"]]
    b_hrv = [e["hrv_ms"] for e in c.get("/api/morning", headers=_bearer(b_sess)).json()["history"]]
    assert 80 in a_hrv and 40 not in a_hrv
    assert 40 in b_hrv and 80 not in b_hrv


def test_profile_is_scoped_per_athlete(env):
    c, root = env["client"], env["root"]
    a_sess, a_pid = _new_athlete(c)
    b_sess, b_pid = _new_athlete(c)
    r = c.put(
        "/api/profile",
        headers=_bearer(a_sess),
        json={"ftp": 300, "hr_rest": None, "hr_max": None, "sex": "M", "lthr_pct": 0.88},
    )
    assert r.status_code == 200
    assert c.get("/api/profile", headers=_bearer(a_sess)).json()["ftp"] == 300
    assert c.get("/api/profile", headers=_bearer(b_sess)).json() is None
    assert (root / a_pid / "profile.yaml").exists()
    assert not (root / b_pid / "profile.yaml").exists()


def test_no_env_ftp_leak_to_athlete(env, monkeypatch):
    monkeypatch.setenv("STRAVA_FTP", "999")
    monkeypatch.setenv("STRAVA_HR_REST", "45")
    monkeypatch.setenv("STRAVA_HR_MAX", "190")
    c = env["client"]
    sess, _ = _new_athlete(c)
    proj = c.get("/api/metrics/ftp-projection", headers=_bearer(sess)).json()
    assert proj["current_ftp"] == 250.0  # défaut en dur, pas la valeur env (999)


def test_athlete_without_tokens_503_on_detail(env):
    c, root = env["client"], env["root"]
    sess, pid = _new_athlete(c)
    _seed_activity(root, pid, strava_id=42, training_load=50.0,
                   date_iso=dt.date.today().isoformat())
    assert c.get("/api/activities", headers=_bearer(sess)).status_code == 200
    # Le détail exige un client Strava : athlète sans tokens (avant 1c) → 503.
    assert c.get("/api/activities/42", headers=_bearer(sess)).status_code == 503


def test_strava_status_reachable_and_isolated(env):
    c = env["client"]
    a_sess, _ = _new_athlete(c)
    b_sess, _ = _new_athlete(c)
    # strava est dégaté (plus de 403) et scopé par athlète.
    assert c.get("/api/strava/sync-status", headers=_bearer(a_sess)).status_code == 200
    # A lance un sync (sans tokens Strava → finira en erreur) ; B reste idle.
    assert c.post("/api/strava/sync", headers=_bearer(a_sess)).status_code == 200
    a_status = c.get("/api/strava/sync-status", headers=_bearer(a_sess)).json()["status"]
    b_status = c.get("/api/strava/sync-status", headers=_bearer(b_sess)).json()["status"]
    assert a_status != "idle"
    assert b_status == "idle"


def test_residual_auth(env):
    c = env["client"]
    assert c.get("/api/metrics/load").status_code == 401  # pas de header
    sess, _ = _new_athlete(c)
    # Routeur encore gaté coach-only → athlète refusé.
    assert c.get("/api/coach/sessions", headers=_bearer(sess)).status_code == 403
