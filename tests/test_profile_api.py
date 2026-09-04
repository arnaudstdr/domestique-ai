"""Tests des endpoints ``/api/profile``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "profile_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "profile.yaml"))
    monkeypatch.delenv("STRAVA_FTP", raising=False)
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    monkeypatch.delenv("STRAVA_SEX", raising=False)
    monkeypatch.delenv("STRAVA_LTHR_PCT", raising=False)

    from domestique_ai.config import invalidate_profile_cache

    invalidate_profile_cache()
    init_db(db)

    from domestique_ai.api.main import app

    with TestClient(app) as c:
        yield c


def test_get_profile_empty(client: TestClient) -> None:
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.json() is None


def test_put_profile_persists_and_roundtrips(client: TestClient) -> None:
    payload = {
        "ftp": 285,
        "hr_rest": 48,
        "hr_max": 192,
        "sex": "M",
        "lthr_pct": 0.87,
    }
    with patch("domestique_ai.api.routers.profile.recalculate_training_loads") as mock_recalc:
        r = client.put("/api/profile", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ftp"] == 285
    assert body["hr_rest"] == 48
    assert body["lthr_pct"] == 0.87

    # Le recalcul a été déclenché (champs HR ont changé depuis "aucun profil").
    assert mock_recalc.called

    # GET doit relire le YAML qu'on vient d'écrire.
    r2 = client.get("/api/profile")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ftp"] == 285
    assert body2["hr_max"] == 192


def test_put_profile_rejects_invalid_lthr(client: TestClient) -> None:
    r = client.put(
        "/api/profile",
        json={"ftp": 250, "lthr_pct": 1.5},
    )
    assert r.status_code == 422


def test_put_profile_rejects_invalid_sex(client: TestClient) -> None:
    r = client.put(
        "/api/profile",
        json={"ftp": 250, "sex": "Z"},
    )
    assert r.status_code == 422


def test_put_profile_no_recalc_when_only_ftp_changes(client: TestClient) -> None:
    """Modifier uniquement la FTP ne doit pas déclencher le recalcul hr-TSS."""
    # 1ère écriture : pose la baseline (HR/sex/lthr aux valeurs par défaut).
    client.put(
        "/api/profile",
        json={"ftp": 250, "sex": "M", "lthr_pct": 0.88},
    )
    # 2ᵉ écriture : seule la FTP change.
    with patch("domestique_ai.api.routers.profile.recalculate_training_loads") as mock_recalc:
        r = client.put(
            "/api/profile",
            json={"ftp": 290, "sex": "M", "lthr_pct": 0.88},
        )
    assert r.status_code == 200
    assert not mock_recalc.called


def test_put_profile_triggers_recalc_when_hr_changes(client: TestClient) -> None:
    """Modifier HR rest doit déclencher le recalcul même si la baseline existait."""
    client.put(
        "/api/profile",
        json={"ftp": 250, "hr_rest": 50, "hr_max": 190},
    )
    with patch("domestique_ai.api.routers.profile.recalculate_training_loads") as mock_recalc:
        r = client.put(
            "/api/profile",
            json={"ftp": 250, "hr_rest": 45, "hr_max": 190},
        )
    assert r.status_code == 200
    assert mock_recalc.called
