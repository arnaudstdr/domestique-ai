"""Tests de la prescription de séances par le coach (roster).

Couvre le storage (reconstruction Workout, round-trip), la priorité d'une
prescription sur le plan dans la lecture, et les endpoints coach/athlète avec
l'auth réelle (calqué sur test_coach_roster_api.py).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import auth as auth_router
from domestique_ai.api.routers import metrics as metrics_router
from domestique_ai.api.routers import plan as plan_router
from domestique_ai.api.routers import prescriptions as prescriptions_router
from domestique_ai.api.routers import roster as roster_router
from domestique_ai.llm.prescription_storage import (
    PrescriptionError,
    get_prescription_for_date,
    list_prescriptions,
    save_prescription,
    workout_from_choice,
)

_LEGACY = "legacy-presc-token"


# --- Storage (sans réseau) ----------------------------------------------------


def test_workout_from_choice_builds_consistent_workout():
    w = workout_from_choice("2026-06-12", "intervals", 60, "bloc seuil")
    assert w.kind == "intervals"
    assert w.target_zone == "z4"
    assert w.duration_min == 60
    assert w.estimated_tss > 0
    assert w.structure  # steps reconstruits
    assert w.notes == "bloc seuil"


def test_workout_from_choice_rejects_bad_input():
    with pytest.raises(PrescriptionError):
        workout_from_choice("2026-06-12", "vo2max", 60)
    with pytest.raises(PrescriptionError):
        workout_from_choice("pas-une-date", "endurance", 60)


def test_workout_from_choice_floors_duration():
    w = workout_from_choice("2026-06-12", "recovery", 5)
    assert w.duration_min == 20  # plancher


def test_storage_round_trip(tmp_path: Path):
    db = tmp_path / "athlete.db"
    row = save_prescription("2026-06-12", "tempo", 50, "test", created_by="coach-1", db_path=db)
    assert row["id"] > 0
    assert row["created_by"] == "coach-1"

    listed = list_prescriptions(db_path=db)
    assert len(listed) == 1
    assert listed[0]["date"] == "2026-06-12"

    found = get_prescription_for_date("2026-06-12", db_path=db)
    assert found is not None and found.kind == "tempo"
    assert get_prescription_for_date("2026-06-13", db_path=db) is None


def test_latest_prescription_wins_for_date(tmp_path: Path):
    db = tmp_path / "athlete.db"
    save_prescription("2026-06-12", "endurance", 90, db_path=db)
    save_prescription("2026-06-12", "intervals", 60, db_path=db)
    found = get_prescription_for_date("2026-06-12", db_path=db)
    assert found is not None and found.kind == "intervals"  # la plus récente (id DESC)


# --- Priorité prescription dans la lecture ------------------------------------


def test_prescription_overrides_plan_in_get_planned_workout(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    from domestique_ai.athlete_context import context_from_env
    from domestique_ai.llm.plan_storage import save_plan
    from domestique_ai.llm.tools import get_planned_workout
    from domestique_ai.processing.plan_builder import build_training_plan

    db = tmp_path / "athlete.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    ctx = context_from_env()

    target = dt.date.today()
    # Un plan déterministe couvrant aujourd'hui.
    plan = build_training_plan(
        target_date=None, ctl_current=30.0, sessions_per_week=5, start_date=target
    )
    assert plan
    save_plan(plan, sessions_per_week=5, db_path=db)

    # Sans prescription : source plan (ou hors fenêtre).
    base = get_planned_workout(target.isoformat(), ctx=ctx)
    # Avec prescription ce jour-là : elle prime.
    save_prescription(target.isoformat(), "recovery", 30, db_path=db)
    res = get_planned_workout(target.isoformat(), ctx=ctx)
    assert res["available"] is True
    assert res["source"] == "prescription"
    assert res["planned_workout"]["kind"] == "recovery"
    # base n'était pas une prescription
    assert base.get("source") != "prescription"


# --- API coach / athlète ------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=_LEGACY)
    app.include_router(auth_router.router)
    app.include_router(metrics_router.router)
    app.include_router(plan_router.router)
    app.include_router(roster_router.router)
    app.include_router(prescriptions_router.router)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def env(tmp_path: Path, monkeypatch) -> Iterator[dict]:
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    with TestClient(_make_app()) as c:
        yield {"client": c, "root": tmp_path / "athletes"}


def _new_athlete(client: TestClient) -> tuple[str, str]:
    inv = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "athlete"})
    acc = client.post("/api/auth/accept-invite", json={"invite_token": inv.json()["invite_token"]})
    session = acc.json()["session_token"]
    me = client.get("/api/auth/me", headers=_bearer(session))
    return session, me.json()["public_id"]


def _new_coach(client: TestClient) -> str:
    inv = client.post("/api/auth/invitations", headers=_bearer(_LEGACY), json={"role": "coach"})
    acc = client.post("/api/auth/accept-invite", json={"invite_token": inv.json()["invite_token"]})
    return acc.json()["session_token"]


def test_coach_prescribes_for_own_athlete(env):
    c = env["client"]
    a_sess, a_pid = _new_athlete(c)
    body = {"date": "2026-06-12", "kind": "intervals", "duration_min": 60, "notes": "seuil"}
    r = c.post(f"/api/roster/athletes/{a_pid}/prescriptions", headers=_bearer(_LEGACY), json=body)
    assert r.status_code == 201, r.text
    assert r.json()["workout"]["kind"] == "intervals"
    assert r.json()["created_by"]  # le public_id du coach bootstrap

    # L'athlète voit sa prescription.
    seen = c.get("/api/prescriptions", headers=_bearer(a_sess)).json()
    assert len(seen) == 1 and seen[0]["date"] == "2026-06-12"


def test_coach_lists_and_deletes_prescription(env):
    c = env["client"]
    _, a_pid = _new_athlete(c)
    created = c.post(
        f"/api/roster/athletes/{a_pid}/prescriptions",
        headers=_bearer(_LEGACY),
        json={"date": "2026-06-12", "kind": "tempo", "duration_min": 45},
    ).json()
    listed = c.get(f"/api/roster/athletes/{a_pid}/prescriptions", headers=_bearer(_LEGACY))
    assert listed.status_code == 200 and len(listed.json()) == 1
    d = c.delete(
        f"/api/roster/athletes/{a_pid}/prescriptions/{created['id']}",
        headers=_bearer(_LEGACY),
    )
    assert d.status_code == 204
    assert (
        c.get(f"/api/roster/athletes/{a_pid}/prescriptions", headers=_bearer(_LEGACY)).json() == []
    )


def test_prescription_forbidden_out_of_roster(env):
    c = env["client"]
    _, a_pid = _new_athlete(c)
    other_coach = _new_coach(c)  # n'a pas cet athlète
    r = c.post(
        f"/api/roster/athletes/{a_pid}/prescriptions",
        headers=_bearer(other_coach),
        json={"date": "2026-06-12", "kind": "endurance", "duration_min": 90},
    )
    assert r.status_code == 403


def test_prescription_forbidden_for_athlete(env):
    c = env["client"]
    a_sess, a_pid = _new_athlete(c)
    b_sess, _ = _new_athlete(c)
    # B (athlète) ne peut pas prescrire à A.
    r = c.post(
        f"/api/roster/athletes/{a_pid}/prescriptions",
        headers=_bearer(b_sess),
        json={"date": "2026-06-12", "kind": "endurance", "duration_min": 90},
    )
    assert r.status_code == 403


def test_coach_assigns_plan_to_athlete(env):
    c = env["client"]
    a_sess, a_pid = _new_athlete(c)
    r = c.post(
        f"/api/roster/athletes/{a_pid}/plan",
        headers=_bearer(_LEGACY),
        json={"sessions_per_week": 4},
    )
    assert r.status_code == 201, r.text
    assert r.json()["workouts"]  # plan non vide
    # Le plan est dans la DB de l'athlète.
    plans = c.get("/api/plan", headers=_bearer(a_sess)).json()
    assert len(plans) == 1


def test_assign_plan_forbidden_out_of_roster(env):
    c = env["client"]
    _, a_pid = _new_athlete(c)
    other_coach = _new_coach(c)
    r = c.post(
        f"/api/roster/athletes/{a_pid}/plan",
        headers=_bearer(other_coach),
        json={"sessions_per_week": 4},
    )
    assert r.status_code == 403


def test_impersonation_plan_post_still_read_only(env):
    """Non-régression : la garde lecture seule du POST /api/plan?athlete= reste active."""
    c = env["client"]
    _, a_pid = _new_athlete(c)
    r = c.post(
        "/api/plan",
        headers=_bearer(_LEGACY),
        params={"athlete": a_pid},
        json={"sessions_per_week": 4},
    )
    assert r.status_code == 403
