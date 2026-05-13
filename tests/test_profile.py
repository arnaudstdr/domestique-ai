"""Tests du module ``domestique_ai.llm.profile``."""

from __future__ import annotations

import pytest

from domestique_ai.llm.profile import (
    Profile,
    ProfileError,
    load_profile,
    save_profile,
)


def _patch_path(tmp_path, monkeypatch, content: str | None = None):
    target = tmp_path / "profile.yaml"
    if content is not None:
        target.write_text(content)
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(target))
    # On invalide le cache du module config pour que chaque test parte propre.
    from domestique_ai.config import invalidate_profile_cache

    invalidate_profile_cache()
    return target


def test_load_returns_none_when_file_missing(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content=None)
    assert load_profile() is None


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch)
    original = Profile(ftp=280, hr_rest=48, hr_max=192, sex="M", lthr_pct=0.87)
    save_profile(original)

    loaded = load_profile()
    assert loaded is not None
    assert loaded.ftp == 280
    assert loaded.hr_rest == 48
    assert loaded.hr_max == 192
    assert loaded.sex == "M"
    assert loaded.lthr_pct == pytest.approx(0.87)


def test_load_rejects_invalid_sex(tmp_path, monkeypatch):
    _patch_path(
        tmp_path,
        monkeypatch,
        content="ftp: 250\nsex: X\n",
    )
    with pytest.raises(ProfileError, match="sex invalide"):
        load_profile()


def test_load_rejects_negative_ftp(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content="ftp: -10\n")
    with pytest.raises(ProfileError, match="ftp"):
        load_profile()


def test_load_rejects_lthr_out_of_range(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content="lthr_pct: 1.5\n")
    with pytest.raises(ProfileError, match="lthr_pct hors borne"):
        load_profile()


def test_load_rejects_non_dict(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content="- not\n- a\n- dict\n")
    with pytest.raises(ProfileError, match="dictionnaire YAML"):
        load_profile()


def test_partial_profile_loads_with_defaults(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content="ftp: 230\n")
    loaded = load_profile()
    assert loaded is not None
    assert loaded.ftp == 230
    assert loaded.hr_rest is None
    assert loaded.hr_max is None
    assert loaded.sex == "M"
    assert loaded.lthr_pct == pytest.approx(0.88)


def test_to_dict_skips_none(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch)
    profile = Profile(ftp=250)
    payload = profile.to_dict()
    assert "hr_rest" not in payload
    assert "hr_max" not in payload
    assert payload["ftp"] == 250
    assert payload["sex"] == "M"
    assert payload["lthr_pct"] == pytest.approx(0.88)


def test_sex_lowercase_is_coerced(tmp_path, monkeypatch):
    _patch_path(tmp_path, monkeypatch, content="sex: f\n")
    loaded = load_profile()
    assert loaded is not None
    assert loaded.sex == "F"
