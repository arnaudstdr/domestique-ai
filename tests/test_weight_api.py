"""Tests des endpoints ``/api/morning/weight`` (poids + rapport W/kg)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "weight_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "profile.yaml"))
    monkeypatch.delenv("STRAVA_FTP", raising=False)

    from domestique_ai.config import invalidate_profile_cache

    invalidate_profile_cache()
    init_db(db)

    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def test_get_weight_empty(client: TestClient) -> None:
    r = client.get("/api/morning/weight")
    assert r.status_code == 200
    body = r.json()
    assert body["weight_kg"] is None
    assert body["date"] is None
    assert body["wkg"] is None
    # FTP par défaut (250 W) exposée même sans poids.
    assert body["ftp_w"] == 250.0


def test_put_weight_returns_wkg(client: TestClient) -> None:
    with patch("domestique_ai.api.routers.profile.recalculate_training_loads"):
        client.put("/api/profile", json={"ftp": 280, "sex": "M", "lthr_pct": 0.88})

    r = client.put("/api/morning/weight", json={"weight_kg": 70.0, "date": "2026-05-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["weight_kg"] == 70.0
    assert body["date"] == "2026-05-01"
    assert body["ftp_w"] == 280.0
    assert body["wkg"] == 4.0

    # GET renvoie le dernier poids.
    r2 = client.get("/api/morning/weight")
    assert r2.json()["weight_kg"] == 70.0


def test_put_weight_rejects_invalid(client: TestClient) -> None:
    assert client.put("/api/morning/weight", json={"weight_kg": 0}).status_code == 422
    assert client.put("/api/morning/weight", json={"weight_kg": -5}).status_code == 422


def test_put_weight_does_not_clobber_other_metrics(client: TestClient) -> None:
    client.post("/api/morning", json={"date": "2026-05-01", "hrv_ms": 55.0, "sleep_hours": 7.5})
    client.put("/api/morning/weight", json={"weight_kg": 71.0, "date": "2026-05-01"})

    entries = client.get("/api/morning?days=365").json()["history"]
    entry = next(e for e in entries if e["date"] == "2026-05-01")
    assert entry["weight_kg"] == 71.0
    assert entry["hrv_ms"] == 55.0
    assert entry["sleep_hours"] == 7.5
