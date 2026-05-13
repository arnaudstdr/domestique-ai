"""Tests de la priorité YAML profil > .env > défaut dans ``config.py``."""

from __future__ import annotations

import pytest

from domestique_ai.config import (
    get_ftp,
    get_hr_max,
    get_hr_rest,
    get_lthr_pct,
    get_sex,
    invalidate_profile_cache,
)


def _set_profile(tmp_path, monkeypatch, content: str):
    target = tmp_path / "profile.yaml"
    target.write_text(content)
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(target))
    invalidate_profile_cache()


def _clear_profile_env(tmp_path, monkeypatch):
    # Pointe vers un fichier inexistant pour que `_profile_or_none` retourne None.
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "missing.yaml"))
    invalidate_profile_cache()


def test_ftp_falls_back_to_env(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.setenv("STRAVA_FTP", "275")
    assert get_ftp() == 275.0


def test_ftp_default_when_nothing_set(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.delenv("STRAVA_FTP", raising=False)
    assert get_ftp() == 250.0


def test_ftp_yaml_wins_over_env(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "ftp: 310\n")
    monkeypatch.setenv("STRAVA_FTP", "199")
    assert get_ftp() == 310.0


def test_hr_rest_yaml_wins_over_env(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "hr_rest: 47\n")
    monkeypatch.setenv("STRAVA_HR_REST", "60")
    assert get_hr_rest() == 47.0


def test_hr_rest_falls_back_to_env_when_not_in_yaml(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "ftp: 250\n")  # hr_rest absent
    monkeypatch.setenv("STRAVA_HR_REST", "55")
    assert get_hr_rest() == 55.0


def test_hr_max_yaml_wins(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "hr_max: 198\n")
    monkeypatch.setenv("STRAVA_HR_MAX", "180")
    assert get_hr_max() == 198.0


def test_hr_max_none_when_nothing_set(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    assert get_hr_max() is None


def test_sex_yaml_wins(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "sex: F\n")
    monkeypatch.setenv("STRAVA_SEX", "M")
    assert get_sex() == "F"


def test_sex_falls_back_to_env(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.setenv("STRAVA_SEX", "F")
    assert get_sex() == "F"


def test_sex_default_M(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.delenv("STRAVA_SEX", raising=False)
    assert get_sex() == "M"


def test_lthr_pct_yaml_wins(tmp_path, monkeypatch):
    _set_profile(tmp_path, monkeypatch, "lthr_pct: 0.91\n")
    monkeypatch.setenv("STRAVA_LTHR_PCT", "0.80")
    assert get_lthr_pct() == pytest.approx(0.91)


def test_lthr_pct_default(tmp_path, monkeypatch):
    _clear_profile_env(tmp_path, monkeypatch)
    monkeypatch.delenv("STRAVA_LTHR_PCT", raising=False)
    assert get_lthr_pct() == pytest.approx(0.88)


def test_cache_invalidation_after_file_rewrite(tmp_path, monkeypatch):
    """Le cache doit relire si le fichier change (mtime change)."""
    target = tmp_path / "profile.yaml"
    target.write_text("ftp: 200\n")
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(target))
    invalidate_profile_cache()
    assert get_ftp() == 200.0

    # Réécriture du fichier avec un mtime distinct.
    import os
    import time

    # On force un mtime supérieur (sécurise les FS à granularité faible).
    new_mtime = time.time() + 2
    target.write_text("ftp: 320\n")
    os.utime(target, (new_mtime, new_mtime))

    assert get_ftp() == 320.0


def test_invalid_yaml_falls_back_to_env(tmp_path, monkeypatch):
    """YAML invalide → cache None, fallback `.env` propre."""
    target = tmp_path / "profile.yaml"
    target.write_text("ftp: not_a_number\n")
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(target))
    invalidate_profile_cache()
    monkeypatch.setenv("STRAVA_FTP", "260")
    assert get_ftp() == 260.0
