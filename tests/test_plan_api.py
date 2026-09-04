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
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "plan_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "obj.yaml"))
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "avail.yaml"))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
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


def test_get_active_plan(client: TestClient) -> None:
    created = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    r = client.get("/api/plan/active")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["status"] == "active"
    assert body["start_date"] is not None
    assert body["adapt_reason"] is None


def test_get_active_plan_none(client: TestClient) -> None:
    r = client.get("/api/plan/active")
    assert r.status_code == 404


def test_new_plan_supersedes_previous(client: TestClient) -> None:
    first = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    second = client.post("/api/plan", json={"sessions_per_week": 3}).json()
    # L'ancien plan devient superseded.
    r1 = client.get(f"/api/plan/{first['id']}")
    assert r1.json()["status"] == "superseded"
    r2 = client.get(f"/api/plan/{second['id']}")
    assert r2.json()["status"] == "active"


def test_plan_versions_lineage(client: TestClient) -> None:
    first = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    r = client.get(f"/api/plan/{first['id']}/versions")
    assert r.status_code == 200
    versions = r.json()
    assert versions[0]["id"] == first["id"]
    assert versions[0]["parent_plan_id"] is None


def test_weekly_review_replans(client: TestClient) -> None:
    first = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    r = client.post("/api/plan/weekly-review")
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is False
    assert body["replanned"] is True
    assert body["parent_plan_id"] == first["id"]
    assert body["new_plan_id"] != first["id"]
    # Le nouveau plan est actif, l'ancien superseded.
    old = client.get(f"/api/plan/{first['id']}").json()
    assert old["status"] == "superseded"
    new = client.get(f"/api/plan/{body['new_plan_id']}").json()
    assert new["status"] == "active"
    assert new["adapt_reason"]


def test_weekly_review_skips_if_already_run(client: TestClient) -> None:
    client.post("/api/plan", json={"sessions_per_week": 4})
    r1 = client.post("/api/plan/weekly-review")
    assert r1.json()["replanned"] is True
    r2 = client.post("/api/plan/weekly-review")
    assert r2.status_code == 200
    assert r2.json()["skipped"] is True


def test_manual_decision_override(client: TestClient) -> None:
    created = client.post("/api/plan", json={"sessions_per_week": 4}).json()
    first_date = created["workouts"][0]["date"]
    r = client.post(
        "/api/plan/decision",
        json={"date": first_date, "decision": "rest", "reason": "Nuit blanche"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "rest"
    assert body["decided_by"] == "user"
    assert body["reason"] == "Nuit blanche"
    # La décision est listée sur le plan.
    r2 = client.get(f"/api/plan/{created['id']}/decisions")
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["date"] == first_date


def test_manual_decision_out_of_window(client: TestClient) -> None:
    client.post("/api/plan", json={"sessions_per_week": 4})
    r = client.post(
        "/api/plan/decision",
        json={"date": "2030-01-01", "decision": "rest"},
    )
    assert r.status_code == 422
