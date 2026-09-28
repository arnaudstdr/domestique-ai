"""Suppression d'un athlète du roster par le coach.

Couvre ``delete_user`` (DB plateforme + cascade) et l'endpoint
``DELETE /api/roster/athletes/{public_id}`` (nettoyage disque inclus).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.routers import roster as roster_router
from domestique_ai.platform_db import (
    create_user,
    get_user_by_id,
    get_user_by_public_id,
    link_coach_athlete,
)


@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(BearerAuthMiddleware, token="legacy-token")
    application.include_router(roster_router.router)
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer legacy-token"}


def test_delete_user_removes_row_and_links(tmp_path: Path):
    from domestique_ai.platform_db import delete_user, init_platform_db, list_athletes_for_coach

    path = tmp_path / "platform.db"
    init_platform_db(path)
    coach = create_user("coach", "Coach", path=path)
    athlete = create_user("athlete", "Test", path=path)
    link_coach_athlete(coach["id"], athlete["id"], path=path)

    assert len(list_athletes_for_coach(coach["id"], path=path)) == 1
    assert delete_user(athlete["id"], path=path) is True
    assert get_user_by_public_id(athlete["public_id"], path=path) is None
    assert list_athletes_for_coach(coach["id"], path=path) == []


def test_delete_user_refuses_bootstrap(tmp_path: Path):
    from domestique_ai.platform_db import delete_user, init_platform_db

    path = tmp_path / "platform.db"
    init_platform_db(path)
    boot = create_user("coach", "Owner", is_bootstrap=True, path=path)
    with pytest.raises(ValueError):
        delete_user(boot["id"], path=path)


def test_delete_user_unknown_returns_false(tmp_path: Path):
    from domestique_ai.platform_db import delete_user, init_platform_db

    path = tmp_path / "platform.db"
    init_platform_db(path)
    assert delete_user(9999, path=path) is False


def test_delete_roster_athlete_removes_account_and_data_dir(client: TestClient):
    from domestique_ai.config import get_athletes_root
    from domestique_ai.platform_db import get_or_create_bootstrap_coach

    boot = get_or_create_bootstrap_coach()
    athlete = create_user("athlete", "Matthieu")
    link_coach_athlete(boot["id"], athlete["id"])

    # Simule un espace données sur disque.
    athlete_dir = get_athletes_root() / athlete["public_id"]
    athlete_dir.mkdir(parents=True, exist_ok=True)
    (athlete_dir / "strava_activities.db").write_text("x", encoding="utf-8")

    r = client.delete(f"/api/roster/athletes/{athlete['public_id']}", headers=_headers())
    assert r.status_code == 204
    assert get_user_by_public_id(athlete["public_id"]) is None
    assert not athlete_dir.exists()


def test_delete_roster_athlete_outside_roster_403(client: TestClient):
    outsider = create_user("athlete", "NotMine")
    r = client.delete(f"/api/roster/athletes/{outsider['public_id']}", headers=_headers())
    assert r.status_code == 403
    assert get_user_by_id(outsider["id"]) is not None


def test_delete_roster_athlete_unknown_403(client: TestClient):
    r = client.delete("/api/roster/athletes/doesnotexist", headers=_headers())
    assert r.status_code == 403
