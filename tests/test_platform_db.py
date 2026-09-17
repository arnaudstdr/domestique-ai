"""Tests pour la DB plateforme (identité multi-tenant)."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai import platform_db as pdb


def _past() -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()


def _future() -> str:
    return (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def _init_db():
    pdb.init_platform_db()


def test_init_platform_db_idempotent():
    pdb.init_platform_db()
    pdb.init_platform_db()  # ne doit pas lever


def test_create_user_rejects_invalid_role():
    with pytest.raises(ValueError):
        pdb.create_user(role="superhero")


def test_create_user_returns_public_id_without_hash():
    user = pdb.create_user(role="athlete", display_name="Alice")
    assert user["role"] == "athlete"
    assert user["display_name"] == "Alice"
    assert user["public_id"]
    assert "token_hash" not in user


def test_bootstrap_coach_is_unique_and_idempotent():
    a = pdb.get_or_create_bootstrap_coach()
    b = pdb.get_or_create_bootstrap_coach()
    assert a["id"] == b["id"]
    assert a["role"] == "coach"
    assert a["is_bootstrap"] is True


def test_invitation_create_then_accept_flow():
    coach = pdb.get_or_create_bootstrap_coach()
    inv, token = pdb.create_invitation(created_by=coach["id"], role="athlete")
    assert inv["status"] == "pending"
    assert token  # token clair renvoyé une fois

    user, session_token = pdb.accept_invitation(token, display_name="Bob")
    assert user["role"] == "athlete"
    assert session_token

    # Statut passé à accepted.
    invitations = pdb.list_invitations(created_by=coach["id"])
    assert invitations[0]["status"] == "accepted"
    assert invitations[0]["accepted_user_id"] == user["id"]

    # Lien coach↔athlète créé.
    athletes = pdb.list_athletes_for_coach(coach["id"])
    assert [a["id"] for a in athletes] == [user["id"]]

    # La session renvoyée résout bien vers l'athlète.
    resolved = pdb.resolve_session_token(session_token)
    assert resolved is not None and resolved["id"] == user["id"]


def test_invitation_cannot_be_accepted_twice():
    inv, token = pdb.create_invitation(created_by=None, role="athlete")
    pdb.accept_invitation(token)
    with pytest.raises(pdb.InvitationError):
        pdb.accept_invitation(token)


def test_invitation_unknown_token_raises():
    with pytest.raises(pdb.InvitationError):
        pdb.accept_invitation("nonexistent-token")


def test_invitation_expired_raises():
    inv, token = pdb.create_invitation(created_by=None, role="athlete", expires_at=_past())
    with pytest.raises(pdb.InvitationError):
        pdb.accept_invitation(token)


def test_coach_invite_does_not_link_when_role_is_coach():
    coach = pdb.get_or_create_bootstrap_coach()
    _, token = pdb.create_invitation(created_by=coach["id"], role="coach")
    user, _ = pdb.accept_invitation(token)
    assert user["role"] == "coach"
    # Pas de lien coach_athlete (l'invité est lui-même coach).
    assert pdb.list_athletes_for_coach(coach["id"]) == []


def test_list_users_all_and_filtered_by_role():
    coach = pdb.get_or_create_bootstrap_coach()
    alice = pdb.create_user(role="athlete", display_name="Alice")
    bob = pdb.create_user(role="athlete", display_name="Bob")

    all_ids = {u["id"] for u in pdb.list_users()}
    assert all_ids == {coach["id"], alice["id"], bob["id"]}

    athletes = pdb.list_users(role="athlete")
    assert {u["id"] for u in athletes} == {alice["id"], bob["id"]}

    coaches = pdb.list_users(role="coach")
    assert [u["id"] for u in coaches] == [coach["id"]]


def test_session_resolve_valid_invalid_revoked_expired():
    user = pdb.create_user(role="athlete")

    _, valid = pdb.create_session(user["id"])
    assert pdb.resolve_session_token(valid)["id"] == user["id"]

    assert pdb.resolve_session_token("bogus") is None

    _, to_revoke = pdb.create_session(user["id"])
    assert pdb.revoke_session(to_revoke) is True
    assert pdb.resolve_session_token(to_revoke) is None

    _, expired = pdb.create_session(user["id"], expires_at=_past())
    assert pdb.resolve_session_token(expired) is None

    _, future = pdb.create_session(user["id"], expires_at=_future())
    assert pdb.resolve_session_token(future)["id"] == user["id"]


# ---------------------------------------------------------------------------
# Lot 1 — migration additive, credentials, 2FA, lockout
# ---------------------------------------------------------------------------


def test_migration_adds_columns_and_preserves_existing_data(tmp_path):
    """Une base « ancien schéma » migre sans perdre de lignes ni de tokens."""
    import sqlite3

    old_path = tmp_path / "old_platform.db"
    conn = sqlite3.connect(old_path)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            display_name TEXT,
            is_bootstrap INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO users (public_id, role, display_name, is_bootstrap, created_at) "
        "VALUES ('legacy-pid', 'coach', 'Owner', 1, '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    pdb.init_platform_db(old_path)

    cols = {r[1] for r in sqlite3.connect(old_path).execute("PRAGMA table_info(users)")}
    assert {"email", "password_hash", "totp_secret", "totp_enabled", "locked_until"} <= cols

    # La ligne existante est intacte et le nouveau champ a un défaut sûr.
    user = pdb.get_user_by_public_id("legacy-pid", path=old_path)
    assert user is not None
    assert user["display_name"] == "Owner"
    assert user["totp_enabled"] is False


def test_get_user_by_email_is_case_insensitive():
    user = pdb.create_user(role="athlete")
    pdb.set_user_credentials(user["id"], "Alice@Example.com", "hash")
    assert pdb.get_user_by_email("alice@example.com")["id"] == user["id"]
    assert pdb.get_user_by_email("ALICE@EXAMPLE.COM")["id"] == user["id"]
    assert pdb.get_user_by_email("unknown@example.com") is None
    assert pdb.get_user_by_email("") is None


def test_set_user_credentials_rejects_duplicate_email():
    import sqlite3

    a = pdb.create_user(role="athlete")
    b = pdb.create_user(role="athlete")
    pdb.set_user_credentials(a["id"], "dup@example.com", "hash-a")
    with pytest.raises(sqlite3.IntegrityError):
        pdb.set_user_credentials(b["id"], "DUP@example.com", "hash-b")


def test_get_user_credentials_hides_nothing_but_is_separate():
    user = pdb.create_user(role="athlete")
    pdb.set_user_credentials(user["id"], "bob@example.com", "hash")
    creds = pdb.get_user_credentials(user["id"])
    assert creds["password_hash"] == "hash"
    assert creds["email"] == "bob@example.com"
    assert creds["totp_enabled"] is False
    # _user_dict (payloads API) n'expose jamais le hash.
    assert "password_hash" not in user


def test_totp_enable_disable_lifecycle():
    user = pdb.create_user(role="athlete")
    # Sans secret, l'activation échoue.
    assert pdb.enable_totp(user["id"]) is False

    pdb.set_totp_secret(user["id"], "SECRET123")
    assert pdb.get_user_credentials(user["id"])["totp_enabled"] is False
    assert pdb.enable_totp(user["id"]) is True
    assert pdb.get_user_by_id(user["id"])["totp_enabled"] is True

    pdb.disable_totp(user["id"])
    creds = pdb.get_user_credentials(user["id"])
    assert creds["totp_enabled"] is False
    assert creds["totp_secret"] is None


def test_recovery_codes_replace_and_consume():
    user = pdb.create_user(role="athlete")
    pdb.replace_recovery_codes(user["id"], ["h1", "h2", "h3"])
    codes = pdb.list_recovery_codes(user["id"])
    assert len(codes) == 3

    assert pdb.mark_recovery_code_used(codes[0]["id"]) is True
    assert pdb.mark_recovery_code_used(codes[0]["id"]) is False  # déjà consommé
    assert len(pdb.list_recovery_codes(user["id"])) == 2

    # Remplacement purge les anciens codes.
    pdb.replace_recovery_codes(user["id"], ["x1"])
    remaining = pdb.list_recovery_codes(user["id"])
    assert [c["code_hash"] for c in remaining] == ["x1"]


def test_failed_login_lockout_and_clear():
    user = pdb.create_user(role="athlete")
    for _ in range(pdb.MAX_FAILED_ATTEMPTS - 1):
        state = pdb.record_failed_login(user["id"])
        assert state["locked_until"] is None
    state = pdb.record_failed_login(user["id"])
    assert state["locked_until"] is not None
    assert pdb.user_is_locked(state["locked_until"]) is True

    pdb.clear_failed_login(user["id"])
    creds = pdb.get_user_credentials(user["id"])
    assert creds["failed_attempts"] == 0
    assert creds["locked_until"] is None
    assert pdb.user_is_locked(creds["locked_until"]) is False


def test_user_is_locked_handles_none_and_past():
    assert pdb.user_is_locked(None) is False
    assert pdb.user_is_locked(_past()) is False
    assert pdb.user_is_locked(_future()) is True


def test_session_default_ttl_from_env(monkeypatch):
    user = pdb.create_user(role="athlete")
    monkeypatch.setenv("DOMESTIQUE_AI_SESSION_TTL_DAYS", "30")
    session, _ = pdb.create_session(user["id"])
    assert session["expires_at"] is not None

    monkeypatch.setenv("DOMESTIQUE_AI_SESSION_TTL_DAYS", "0")
    eternal, _ = pdb.create_session(user["id"])
    assert eternal["expires_at"] is None

