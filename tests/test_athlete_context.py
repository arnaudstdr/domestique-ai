"""Tests pour `AthleteContext` et `context_from_env`."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from domestique_ai.athlete_context import AthleteContext, context_from_env
from domestique_ai.config import (
    get_availability_path,
    get_db_path,
    get_ftp,
    get_hr_max,
    get_hr_rest,
    get_lthr_pct,
    get_objective_path,
    get_profile_path,
    get_sex,
    get_tokens_path,
    invalidate_profile_cache,
)


def test_context_is_frozen():
    ctx = context_from_env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.ftp = 999.0  # type: ignore[misc]


def test_context_from_env_reproduces_getters_default(monkeypatch):
    # Neutralise toute config locale du dev pour partir d'un état déterministe.
    for var in ("STRAVA_FTP", "STRAVA_HR_REST", "STRAVA_HR_MAX",
                "STRAVA_SEX", "STRAVA_LTHR_PCT", "DOMESTIQUE_AI_PROFILE_PATH"):
        monkeypatch.delenv(var, raising=False)
    invalidate_profile_cache()

    ctx = context_from_env()
    assert ctx.db_path == get_db_path()
    assert ctx.tokens_path == get_tokens_path()
    assert ctx.profile_path == get_profile_path()
    assert ctx.objective_path == get_objective_path()
    assert ctx.availability_path == get_availability_path()
    assert ctx.ftp == get_ftp()
    assert ctx.hr_rest == get_hr_rest()
    assert ctx.hr_max == get_hr_max()
    assert ctx.sex == get_sex()
    assert ctx.lthr_pct == get_lthr_pct()


def test_context_from_env_reflects_env_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_PROFILE_PATH", raising=False)
    invalidate_profile_cache()
    monkeypatch.setenv("STRAVA_FTP", "275")
    monkeypatch.setenv("STRAVA_HR_REST", "48")
    monkeypatch.setenv("STRAVA_HR_MAX", "191")
    monkeypatch.setenv("STRAVA_SEX", "F")
    monkeypatch.setenv("STRAVA_LTHR_PCT", "0.9")
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "x.db"))

    ctx = context_from_env()
    assert ctx.ftp == 275.0
    assert ctx.hr_rest == 48.0
    assert ctx.hr_max == 191.0
    assert ctx.sex == "F"
    assert ctx.lthr_pct == 0.9
    assert ctx.db_path == (tmp_path / "x.db").resolve()
    # Cohérence champ par champ avec les getters sous le même environnement.
    assert ctx.ftp == get_ftp()
    assert ctx.hr_rest == get_hr_rest()


def test_athlete_context_constructible_without_env():
    # Un contexte peut être bâti à la main, sans aucune variable d'env.
    ctx = AthleteContext(
        db_path=Path("/tmp/a.db"),
        tokens_path=Path("/tmp/.tok.json"),
        profile_path=Path("/tmp/profile.yaml"),
        objective_path=Path("/tmp/objective.yaml"),
        availability_path=Path("/tmp/availability.yaml"),
        ftp=300.0,
        hr_rest=50.0,
        hr_max=190.0,
        sex="M",
        lthr_pct=0.88,
    )
    assert ctx.ftp == 300.0
    assert ctx.hr_rest == 50.0
