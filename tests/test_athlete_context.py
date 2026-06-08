"""Tests pour `AthleteContext` et `context_from_env`."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from domestique_ai.athlete_context import (
    AthleteContext,
    context_for_athlete,
    context_from_env,
)
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


def test_context_for_bootstrap_is_context_from_env(monkeypatch):
    for var in ("STRAVA_FTP", "STRAVA_HR_REST", "STRAVA_HR_MAX",
                "STRAVA_SEX", "STRAVA_LTHR_PCT", "DOMESTIQUE_AI_PROFILE_PATH"):
        monkeypatch.delenv(var, raising=False)
    from domestique_ai.config import invalidate_profile_cache
    invalidate_profile_cache()
    owner = {"public_id": "abc", "role": "coach", "is_bootstrap": True}
    assert context_for_athlete(owner) == context_from_env()


def test_context_for_athlete_without_profile_uses_hard_defaults(tmp_path, monkeypatch):
    # Profil HR/FTP en env : ne doit PAS fuiter vers un athlète non-propriétaire.
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    monkeypatch.setenv("STRAVA_FTP", "999")
    monkeypatch.setenv("STRAVA_HR_REST", "40")
    monkeypatch.setenv("STRAVA_HR_MAX", "200")

    user = {"public_id": "alice123", "role": "athlete", "is_bootstrap": False}
    ctx = context_for_athlete(user)

    assert ctx.db_path == tmp_path / "athletes" / "alice123" / "strava_activities.db"
    assert ctx.tokens_path == tmp_path / "athletes" / "alice123" / ".strava_tokens.json"
    assert ctx.profile_path == tmp_path / "athletes" / "alice123" / "profile.yaml"
    # Défauts en dur, pas de fuite env.
    assert ctx.ftp == 250.0
    assert ctx.hr_rest is None
    assert ctx.hr_max is None
    assert ctx.sex == "M"
    assert ctx.lthr_pct == 0.88


def test_context_for_athlete_reads_own_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    root = tmp_path / "athletes" / "bob456"
    root.mkdir(parents=True)
    (root / "profile.yaml").write_text(
        "ftp: 280\nhr_rest: 45\nhr_max: 188\nsex: F\nlthr_pct: 0.9\n"
    )
    user = {"public_id": "bob456", "role": "athlete", "is_bootstrap": False}
    ctx = context_for_athlete(user)
    assert ctx.ftp == 280.0
    assert ctx.hr_rest == 45
    assert ctx.hr_max == 188
    assert ctx.sex == "F"
    assert ctx.lthr_pct == 0.9


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
