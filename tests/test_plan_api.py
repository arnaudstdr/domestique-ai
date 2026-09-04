"""Tests des endpoints `/api/plan/...`.

On utilise la fixture `client` de `tests/test_api.py` via un import explicite.
La logique de génération (`build_training_plan`) est testée par ailleurs ; ici
on couvre uniquement le contrat HTTP (status codes, formes, validation).
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "plan_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "obj.yaml"))
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "avail.yaml"))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app) as c:
        yield c


def test_list_plans_empty(client: TestClient) -> None:
    r = client.get("/api/plan")
    assert r.status_code == 200
    assert r.json() == []


def test_post_plan_generates_and_returns_detail(client: TestClient) -> None:
    r = client.post("/api/plan", json={"sessions_per_week": 4})
    assert r.status_code == 201
    body = r.json()
    assert body["id"] >= 1
    assert body["sessions_per_week"] == 4
    assert len(body["workouts"]) >= 4
    # Chaque workout doit avoir une structure non vide et un kind valide.
    first = body["workouts"][0]
    assert {"date", "name", "kind", "duration_min", "target_zone"} <= set(first)
    assert first["kind"] in {"recovery", "endurance", "tempo", "intervals"}
    assert isinstance(first["structure"], list)


def test_post_plan_rejects_out_of_range_sessions(client: TestClient) -> None:
    r = client.post("/api/plan", json={"sessions_per_week": 8})
    assert r.status_code == 422


def test_get_plan_detail(client: TestClient) -> None:
    created = client.post("/api/plan", json={"sessions_per_week": 3}).json()
    r = client.get(f"/api/plan/{created['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert len(body["workouts"]) == len(created["workouts"])


def test_get_plan_not_found(client: TestClient) -> None:
    r = client.get("/api/plan/99999")
    assert r.status_code == 404


def test_delete_plan(client: TestClient) -> None:
    created = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    r = client.delete(f"/api/plan/{created['id']}")
    assert r.status_code == 204
    # Le second DELETE doit renvoyer 404.
    r2 = client.delete(f"/api/plan/{created['id']}")
    assert r2.status_code == 404


def test_export_plan_zip(client: TestClient) -> None:
    created = client.post("/api/plan", json={"sessions_per_week": 3}).json()
    r = client.get(f"/api/plan/{created['id']}/export.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    # Le ZIP doit contenir un fichier .fit par séance.
    with zipfile.ZipFile(BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert len(names) == len(created["workouts"])
    assert all(name.endswith(".fit") for name in names)


def test_export_plan_zip_not_found(client: TestClient) -> None:
    r = client.get("/api/plan/99999/export.zip")
    assert r.status_code == 404
