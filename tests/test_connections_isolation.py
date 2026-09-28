"""Isolation des connexions Garmin / Google Health par athlète.

Vérifie que les chemins de tokens dépendent du ``public_id`` (bootstrap vs
athlète), que les credentials Garmin sont stockés par athlète sans exposition du
mot de passe, et que le ``state`` OAuth Google Health est signé et non
falsifiable.
"""

from __future__ import annotations

from pathlib import Path

from domestique_ai.athlete_context import AthleteContext, context_for_athlete
from domestique_ai.config import (
    garmin_token_dir_for,
    get_athletes_root,
    get_garmin_token_dir,
    get_google_health_tokens_path,
    google_health_tokens_path_for,
)
from domestique_ai.platform_db import (
    clear_user_garmin_credentials,
    create_user,
    get_user_garmin_credentials,
    set_user_garmin_credentials,
)


def _athlete_ctx(public_id: str) -> AthleteContext:
    root = get_athletes_root() / public_id
    return AthleteContext(
        db_path=root / "strava_activities.db",
        profile_path=root / "profile.yaml",
        objective_path=root / "objective.yaml",
        availability_path=root / "availability.yaml",
        ftp=250.0,
        hr_rest=None,
        hr_max=None,
        sex="M",
        lthr_pct=0.88,
        public_id=public_id,
    )


# ---- Chemins de tokens par athlète -------------------------------------------


def test_garmin_token_dir_bootstrap_uses_legacy_path():
    ctx = _athlete_ctx("")
    assert garmin_token_dir_for(ctx) == get_garmin_token_dir()


def test_garmin_token_dir_is_per_athlete():
    pid = "abc123"
    assert garmin_token_dir_for(_athlete_ctx(pid)) == get_athletes_root() / pid / ".garmin_tokens"


def test_two_athletes_have_distinct_garmin_dirs():
    a = garmin_token_dir_for(_athlete_ctx("aaa"))
    b = garmin_token_dir_for(_athlete_ctx("bbb"))
    assert a != b


def test_google_health_path_bootstrap_uses_legacy_path():
    ctx = _athlete_ctx("")
    assert google_health_tokens_path_for(ctx) == get_google_health_tokens_path()


def test_google_health_path_is_per_athlete():
    pid = "abc123"
    expected = get_athletes_root() / pid / ".google_health_tokens.json"
    assert google_health_tokens_path_for(_athlete_ctx(pid)) == expected


# ---- Credentials Garmin en DB plateforme -------------------------------------


def test_set_get_clear_garmin_credentials(tmp_path: Path):
    path = tmp_path / "platform.db"
    from domestique_ai.platform_db import init_platform_db

    init_platform_db(path)
    user = create_user("athlete", "Bob", path=path)
    uid = user["id"]

    set_user_garmin_credentials(uid, "bob@example.com", "s3cret", path=path)
    assert get_user_garmin_credentials(uid, path=path) == ("bob@example.com", "s3cret")

    clear_user_garmin_credentials(uid, path=path)
    assert get_user_garmin_credentials(uid, path=path) == (None, None)


def test_garmin_password_never_exposed_in_user_dict(tmp_path: Path):
    path = tmp_path / "platform.db"
    from domestique_ai.platform_db import get_user_by_id, init_platform_db

    init_platform_db(path)
    user = create_user("athlete", "Bob", path=path)
    set_user_garmin_credentials(user["id"], "bob@example.com", "s3cret", path=path)

    exposed = get_user_by_id(user["id"], path=path)
    assert exposed is not None
    assert "garmin_password" not in exposed
    assert exposed["garmin_email"] == "bob@example.com"
    assert exposed["has_garmin_credentials"] is True


def test_garmin_credentials_isolated_between_users(tmp_path: Path):
    path = tmp_path / "platform.db"
    from domestique_ai.platform_db import init_platform_db

    init_platform_db(path)
    a = create_user("athlete", "A", path=path)
    b = create_user("athlete", "B", path=path)
    set_user_garmin_credentials(a["id"], "a@example.com", "pw-a", path=path)
    set_user_garmin_credentials(b["id"], "b@example.com", "pw-b", path=path)

    assert get_user_garmin_credentials(a["id"], path=path) == ("a@example.com", "pw-a")
    assert get_user_garmin_credentials(b["id"], path=path) == ("b@example.com", "pw-b")


def test_context_for_athlete_reads_credentials_from_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    from domestique_ai.platform_db import init_platform_db

    init_platform_db()
    user = create_user("athlete", "Bob")
    set_user_garmin_credentials(user["id"], "bob@example.com", "s3cret")

    ctx = context_for_athlete(user)
    assert ctx.public_id == user["public_id"]
    assert ctx.garmin_email == "bob@example.com"
    assert ctx.garmin_password == "s3cret"


# ---- State OAuth Google Health signé ------------------------------------------


def test_signed_state_roundtrip():
    from domestique_ai.api.routers.google_health import _sign_state, _verify_state

    state = _sign_state("abc123")
    assert _verify_state(state) == "abc123"


def test_signed_state_bootstrap_empty_public_id():
    from domestique_ai.api.routers.google_health import _sign_state, _verify_state

    assert _verify_state(_sign_state("")) == ""


def test_signed_state_rejects_tampering():
    from domestique_ai.api.routers.google_health import _sign_state, _verify_state

    state = _sign_state("abc123")
    encoded, _, signature = state.partition(".")
    # public_id modifié, signature conservée.
    tampered = _sign_state("evil99").partition(".")[0] + "." + signature
    assert _verify_state(tampered) != "evil99"
    assert _verify_state("garbage") is None
    assert _verify_state(None) is None


def test_signed_state_rejects_other_secret(monkeypatch):
    from domestique_ai.api.routers.google_health import _sign_state, _verify_state

    state = _sign_state("abc123")
    monkeypatch.setenv("DOMESTIQUE_AI_SESSION_SECRET", "a-completely-different-secret")
    assert _verify_state(state) is None
