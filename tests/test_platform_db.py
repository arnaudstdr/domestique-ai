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


def test_set_user_avatar_roundtrip_and_clear():
    user = pdb.create_user(role="athlete")
    # Par défaut, aucune photo.
    assert user["avatar"] is None

    data_url = "data:image/jpeg;base64,AAAA"
    pdb.set_user_avatar(user["id"], data_url)
    assert pdb.get_user_by_id(user["id"])["avatar"] == data_url
    # La liste des athlètes porte aussi l'avatar (roster coach).
    coach = pdb.get_or_create_bootstrap_coach()
    pdb.link_coach_athlete(coach["id"], user["id"])
    roster = pdb.list_athletes_for_coach(coach["id"])
    assert next(a for a in roster if a["id"] == user["id"])["avatar"] == data_url

    pdb.set_user_avatar(user["id"], None)
    assert pdb.get_user_by_id(user["id"])["avatar"] is None


def test_get_or_create_feed_token_is_stable_and_resolvable():
    user = pdb.create_user(role="athlete")
    assert pdb.get_user_by_feed_token("") is None

    token = pdb.get_or_create_feed_token(user["id"])
    assert token
    # Idempotent : un second appel renvoie le même token.
    assert pdb.get_or_create_feed_token(user["id"]) == token
    # Résolution inverse → le bon utilisateur ; le secret n'est pas exposé.
    resolved = pdb.get_user_by_feed_token(token)
    assert resolved is not None
    assert resolved["public_id"] == user["public_id"]
    assert "feed_token" not in resolved


def test_get_or_create_feed_token_unknown_user_raises():
    with pytest.raises(ValueError):
        pdb.get_or_create_feed_token(999999)


def test_rotate_feed_token_invalidates_previous():
    user = pdb.create_user(role="athlete")
    old = pdb.get_or_create_feed_token(user["id"])
    new = pdb.rotate_feed_token(user["id"])
    assert new != old
    assert pdb.get_user_by_feed_token(old) is None
    assert pdb.get_user_by_feed_token(new)["public_id"] == user["public_id"]


def test_clear_feed_token_disables_feed():
    user = pdb.create_user(role="athlete")
    token = pdb.get_or_create_feed_token(user["id"])
    pdb.clear_feed_token(user["id"])
    assert pdb.get_user_by_feed_token(token) is None


# ---- Inscription (email/password/verification) ------------------------------


def test_create_user_with_credentials_is_unverified_when_asked():
    user = pdb.create_user(
        role="athlete",
        email="  New@B.C ",
        password_hash="hash",
        email_verified=False,
    )
    assert user["email"] == "new@b.c"  # normalisé
    assert user["email_verified"] is False
    assert user["has_password"] is True


def test_create_user_defaults_to_verified_for_legacy():
    user = pdb.create_user(role="athlete")
    assert user["email_verified"] is True


def test_set_email_verified():
    user = pdb.create_user(role="athlete", email="a@b.c", password_hash="h", email_verified=False)
    pdb.set_email_verified(user["id"], True)
    assert pdb.get_user_by_id(user["id"])["email_verified"] is True


# ---- Code d'invitation réutilisable du coach --------------------------------


def test_coach_invite_code_lifecycle():
    coach = pdb.create_user(role="coach")
    code = pdb.get_or_create_coach_invite_code(coach["id"])
    # Idempotent.
    assert pdb.get_or_create_coach_invite_code(coach["id"]) == code
    # Résolution inverse → le coach ; le code n'est pas exposé par _user_dict.
    resolved = pdb.get_user_by_coach_invite_code(code)
    assert resolved is not None and resolved["public_id"] == coach["public_id"]
    assert "coach_invite_code" not in resolved
    # Rotation : l'ancien code meurt.
    new = pdb.rotate_coach_invite_code(coach["id"])
    assert new != code
    assert pdb.get_user_by_coach_invite_code(code) is None
    assert pdb.get_user_by_coach_invite_code(new)["public_id"] == coach["public_id"]


def test_coach_invite_code_rejects_non_coach():
    athlete = pdb.create_user(role="athlete")
    with pytest.raises(ValueError):
        pdb.get_or_create_coach_invite_code(athlete["id"])


# ---- Tokens éphémères (email_verify / password_reset) -----------------------


def test_auth_token_create_consume_single_use():
    user = pdb.create_user(role="athlete", email="a@b.c", password_hash="h")
    _row, token = pdb.create_auth_token(user["id"], "email_verify", _future())
    resolved = pdb.consume_auth_token(token, "email_verify")
    assert resolved is not None and resolved["public_id"] == user["public_id"]
    # Usage unique.
    assert pdb.consume_auth_token(token, "email_verify") is None


def test_auth_token_purpose_is_scoped():
    user = pdb.create_user(role="athlete", email="a@b.c", password_hash="h")
    _row, token = pdb.create_auth_token(user["id"], "email_verify", _future())
    # Mauvais purpose → refusé.
    assert pdb.consume_auth_token(token, "password_reset") is None


def test_auth_token_expired_is_rejected():
    user = pdb.create_user(role="athlete", email="a@b.c", password_hash="h")
    _row, token = pdb.create_auth_token(user["id"], "password_reset", _past())
    assert pdb.consume_auth_token(token, "password_reset") is None


def test_create_auth_token_invalidates_previous_of_same_purpose():
    user = pdb.create_user(role="athlete", email="a@b.c", password_hash="h")
    _r1, first = pdb.create_auth_token(user["id"], "email_verify", _future())
    _r2, second = pdb.create_auth_token(user["id"], "email_verify", _future())
    assert pdb.consume_auth_token(first, "email_verify") is None
    assert pdb.consume_auth_token(second, "email_verify") is not None


# ---- Rattachement d'un athlète existant -------------------------------------


def test_consume_invitation_for_link_existing_athlete():
    coach = pdb.create_user(role="coach")
    athlete = pdb.create_user(role="athlete", email="a@b.c", password_hash="h")
    _inv, token = pdb.create_invitation(created_by=coach["id"], role="athlete")

    inv = pdb.consume_invitation_for_link(token, athlete["id"])
    assert inv["status"] == "accepted"
    assert inv["accepted_user_id"] == athlete["id"]
    # Le lien coach↔athlète est créé, sans nouvel utilisateur.
    roster = pdb.list_athletes_for_coach(coach["id"])
    assert [a["public_id"] for a in roster] == [athlete["public_id"]]
    # Invitation non réutilisable.
    with pytest.raises(pdb.InvitationError):
        pdb.consume_invitation_for_link(token, athlete["id"])


def test_consume_invitation_for_link_rejects_non_athlete_invitation():
    coach = pdb.create_user(role="coach")
    athlete = pdb.create_user(role="athlete")
    _inv, token = pdb.create_invitation(created_by=coach["id"], role="coach")
    with pytest.raises(pdb.InvitationError):
        pdb.consume_invitation_for_link(token, athlete["id"])


def test_revoke_all_sessions():
    user = pdb.create_user(role="athlete")
    _s1, t1 = pdb.create_session(user["id"])
    _s2, t2 = pdb.create_session(user["id"])
    assert pdb.revoke_all_sessions(user["id"]) == 2
    assert pdb.resolve_session_token(t1) is None
    assert pdb.resolve_session_token(t2) is None


# ---- Rôle admin (isolé) et réglages plateforme ------------------------------


def test_admin_role_is_not_self_service():
    """``admin`` est dans ``ALL_ROLES`` mais reste hors des flux self-service."""
    assert pdb.ADMIN_ROLE in pdb.ALL_ROLES
    assert pdb.ADMIN_ROLE not in pdb.VALID_ROLES
    with pytest.raises(ValueError):
        pdb.create_user(role="admin")
    with pytest.raises(ValueError):
        pdb.create_invitation(created_by=None, role="admin")


def test_set_user_role_promotes_and_demotes():
    user = pdb.create_user(role="athlete")
    promoted = pdb.set_user_role(user["public_id"], "admin")
    assert promoted["role"] == "admin"
    assert pdb.get_user_by_public_id(user["public_id"])["role"] == "admin"
    assert pdb.set_user_role(user["public_id"], "coach")["role"] == "coach"


def test_create_account_allows_admin_hors_ligne():
    """``create_account`` (CLI) accepte ``admin``, contrairement à ``create_user``."""
    admin = pdb.create_account("admin", email="admin@x.io", password_hash="h")
    assert admin["role"] == "admin"
    assert admin["is_bootstrap"] is False
    assert pdb.get_user_by_email("admin@x.io")["public_id"] == admin["public_id"]
    with pytest.raises(ValueError):
        pdb.create_account("superhero")


def test_set_user_role_unknown_or_invalid():
    assert pdb.set_user_role("nope", "admin") is None
    user = pdb.create_user(role="athlete")
    with pytest.raises(ValueError):
        pdb.set_user_role(user["public_id"], "superhero")


def test_migration_widens_role_check_and_preserves_data(tmp_path):
    """Une base à l'ancien CHECK (coach/athlete) est élargie à ``admin``."""
    import sqlite3

    old_path = tmp_path / "old_platform.db"
    conn = sqlite3.connect(old_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('coach', 'athlete')),
            display_name TEXT,
            is_bootstrap INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT,
            last_used_at TEXT
        );
        INSERT INTO users (public_id, role, display_name, created_at)
            VALUES ('legacy-athlete', 'athlete', 'Bob', '2026-01-01T00:00:00+00:00');
        INSERT INTO sessions (user_id, token_hash, created_at)
            VALUES (1, 'tok', '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    pdb.init_platform_db(old_path)
    pdb.init_platform_db(old_path)  # idempotent

    # Données et FK préservées.
    user = pdb.get_user_by_public_id("legacy-athlete", path=old_path)
    assert user is not None and user["display_name"] == "Bob"
    sessions = sqlite3.connect(old_path).execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions == 1

    # Le CHECK accepte désormais ``admin``.
    promoted = pdb.set_user_role("legacy-athlete", "admin", path=old_path)
    assert promoted["role"] == "admin"


def test_set_feedback_status_validates_and_updates():
    entry = pdb.insert_feedback(category="idea", message="hi")
    assert entry["status"] == "new"
    updated = pdb.set_feedback_status(entry["id"], "acknowledged")
    assert updated is not None and updated["status"] == "acknowledged"
    assert pdb.list_feedback()[0]["status"] == "acknowledged"
    assert pdb.set_feedback_status(999999, "done") is None
    with pytest.raises(ValueError):
        pdb.set_feedback_status(entry["id"], "bogus")


def test_platform_settings_override_env(monkeypatch):
    monkeypatch.delenv("DOMESTIQUE_AI_SIGNUP_ENABLED", raising=False)
    # Sans override : on retombe sur l'env (défaut False).
    assert pdb.effective_signup_enabled() is False
    pdb.set_setting("signup_enabled", "1")

    assert pdb.get_setting("signup_enabled") == "1"
    assert pdb.list_settings()["signup_enabled"] == "1"
    assert pdb.effective_signup_enabled() is True

    pdb.set_setting("signup_enabled", "0")
    assert pdb.effective_signup_enabled() is False
    assert pdb.get_setting("missing", default="x") == "x"


# ---- Gestion compte admin : sécurité, sessions, liens, audit ----------------


def test_get_user_security_state_hides_secrets():
    user = pdb.create_user(role="athlete")
    pdb.set_totp_secret(user["id"], "SECRET")
    pdb.enable_totp(user["id"])
    state = pdb.get_user_security_state(user["id"])
    assert state["totp_enabled"] is True
    assert state["has_password"] is False
    assert state["failed_attempts"] == 0
    assert "password_hash" not in state and "totp_secret" not in state
    assert pdb.get_user_security_state(999999) is None


def test_list_sessions_active_only_filters_revoked():
    user = pdb.create_user(role="athlete")
    _s1, t1 = pdb.create_session(user["id"])
    _s2, t2 = pdb.create_session(user["id"])
    assert len(pdb.list_sessions(user["id"])) == 2
    assert "token_hash" not in pdb.list_sessions(user["id"])[0]

    pdb.revoke_session(t1)
    active = pdb.list_sessions(user["id"])
    assert len(active) == 1
    assert len(pdb.list_sessions(user["id"], active_only=False)) == 2
    assert pdb.count_active_sessions() == 1


def test_list_coaches_for_athlete():
    coach = pdb.create_user(role="coach")
    athlete = pdb.create_user(role="athlete")
    pdb.link_coach_athlete(coach["id"], athlete["id"])
    coaches = pdb.list_coaches_for_athlete(athlete["id"])
    assert [c["public_id"] for c in coaches] == [coach["public_id"]]


def test_admin_audit_record_and_list():
    actor = pdb.create_account("admin")
    target = pdb.create_user(role="athlete")
    pdb.record_admin_audit(actor, "role_change", target, {"from": "athlete", "to": "coach"})
    entries = pdb.list_admin_audit()
    assert len(entries) == 1
    assert entries[0]["actor_public_id"] == actor["public_id"]
    assert entries[0]["target_public_id"] == target["public_id"]
    assert entries[0]["details"] == {"from": "athlete", "to": "coach"}
    assert pdb.list_admin_audit(limit=0) == []


def test_admin_audit_filters_pagination_and_labels():
    actor = pdb.create_account("admin", display_name="Admin Test")
    other = pdb.create_account("admin", email="other@example.com")
    target = pdb.create_user(role="athlete", email="athlete@example.com")
    pdb.record_admin_audit(actor, "unlock_account", target)
    pdb.record_admin_audit(actor, "role_change", target, {"from": "athlete", "to": "coach"})
    pdb.record_admin_audit(actor, "settings_update", details={"signup_enabled": True})
    pdb.record_admin_audit(other, "logout_all", target, {"revoked": 2})

    entries = pdb.list_admin_audit()
    assert [e["action"] for e in entries] == [
        "logout_all",
        "settings_update",
        "role_change",
        "unlock_account",
    ]
    assert entries[2]["actor_label"] == "Admin Test"
    assert entries[2]["target_label"] == "athlete@example.com"
    assert entries[0]["actor_label"] == "other@example.com"
    assert entries[1]["target_label"] is None

    assert [e["action"] for e in pdb.list_admin_audit(actions=["unlock_account"])] == [
        "unlock_account"
    ]
    assert [e["action"] for e in pdb.list_admin_audit(actor_public_id=other["public_id"])] == [
        "logout_all"
    ]
    assert len(pdb.list_admin_audit(q="athlete@exam")) == 3
    assert len(pdb.list_admin_audit(q="Admin")) == 3
    assert [e["action"] for e in pdb.list_admin_audit(q=other["public_id"][:8])] == ["logout_all"]
    assert pdb.list_admin_audit(q="Admin %") == []  # jokers échappés

    page = pdb.list_admin_audit(limit=2, before_id=entries[2]["id"])
    assert [e["action"] for e in page] == ["unlock_account"]

    assert pdb.list_admin_audit(since=_future()) == []
    assert len(pdb.list_admin_audit(since=_past())) == 4

    pdb.delete_user(target["id"])
    after_delete = pdb.list_admin_audit()
    assert after_delete[2]["target_public_id"] == target["public_id"]
    assert after_delete[2]["target_label"] is None


def test_get_announcement_defaults_and_override():
    assert pdb.get_announcement() == {"maintenance_mode": False, "message": None}
    pdb.set_setting("maintenance_mode", "1")
    pdb.set_setting("broadcast_message", "  Bonjour  ")
    assert pdb.get_announcement() == {"maintenance_mode": True, "message": "Bonjour"}
    pdb.set_setting("broadcast_message", "   ")
    assert pdb.get_announcement()["message"] is None


# ---- Consentements (CGU + données de santé) ---------------------------------


def test_create_user_records_consents():
    user = pdb.create_user(
        role="athlete",
        email="a@b.c",
        terms_version="2026-10-beta",
        health_consent_version="2026-10-beta",
        consent_user_agent="pytest",
    )
    assert user["terms_accepted_at"] is not None
    assert user["terms_accepted_version"] == "2026-10-beta"
    assert user["health_consent_at"] is not None
    assert user["health_consent_withdrawn_at"] is None
    # Sans consentement fourni : aucun horodatage.
    plain = pdb.create_user(role="athlete", email="plain@b.c")
    assert plain["terms_accepted_at"] is None
    assert plain["health_consent_at"] is None


def test_set_and_withdraw_health_consent_roundtrip():
    user = pdb.create_user(role="athlete")
    assert pdb.set_user_consents(
        user["id"],
        terms_version="v1",
        health_consent_version="v1",
        user_agent="ua",
    )
    updated = pdb.get_user_by_id(user["id"])
    assert updated is not None
    assert updated["terms_accepted_version"] == "v1"
    assert updated["health_consent_at"] is not None

    assert pdb.withdraw_health_consent(user["id"])
    withdrawn = pdb.get_user_by_id(user["id"])
    assert withdrawn is not None
    assert withdrawn["health_consent_withdrawn_at"] is not None
    # L'historique du consentement initial est conservé.
    assert withdrawn["health_consent_at"] is not None

    # Un nouveau consentement annule le retrait.
    pdb.set_user_consents(user["id"], health_consent_version="v2")
    reaccepted = pdb.get_user_by_id(user["id"])
    assert reaccepted is not None
    assert reaccepted["health_consent_withdrawn_at"] is None
    assert reaccepted["health_consent_version"] == "v2"

    assert pdb.withdraw_health_consent(999999) is False


def test_delete_user_anonymizes_feedback():
    user = pdb.create_user(role="athlete", email="a@b.c")
    pdb.insert_feedback(
        category="bug",
        message="ça plante",
        user_id=user["id"],
        public_id=user["public_id"],
        role="athlete",
        author_email="a@b.c",
        user_agent="pytest-UA",
    )
    assert pdb.delete_user(user["id"])
    entry = pdb.list_feedback()[0]
    assert entry["message"] == "ça plante"  # contenu conservé pour le produit
    assert entry["author_email"] is None
    assert entry["public_id"] is None
    assert entry["user_agent"] is None


# ---- Rétention ---------------------------------------------------------------


def test_purge_llm_calls_and_admin_audit():
    actor = pdb.create_account("admin")
    pdb.insert_llm_call(label="test", entrypoint="pytest", model="m")
    pdb.record_admin_audit(actor, "settings_update", details={"x": 1})
    assert len(pdb.fetch_llm_calls()) == 1
    assert len(pdb.list_admin_audit()) == 1

    # Rien à purger avec une borne dans le passé.
    assert pdb.purge_llm_calls(_past()) == 0
    assert pdb.purge_admin_audit(_past()) == 0
    # Borne future → tout est purgé.
    assert pdb.purge_llm_calls(_future()) == 1
    assert pdb.purge_admin_audit(_future()) == 1
    assert pdb.fetch_llm_calls() == []
    assert pdb.list_admin_audit() == []
