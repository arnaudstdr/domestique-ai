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


def test_oauth_state_create_then_consume_returns_user():
    user = pdb.create_user(role="athlete", display_name="Alice")
    _row, state = pdb.create_oauth_state(user["id"], expires_at=_future())
    resolved = pdb.consume_oauth_state(state)
    assert resolved is not None and resolved["id"] == user["id"]


def test_oauth_state_is_single_use():
    user = pdb.create_user(role="athlete")
    _row, state = pdb.create_oauth_state(user["id"])
    assert pdb.consume_oauth_state(state) is not None
    assert pdb.consume_oauth_state(state) is None  # rejoué → refusé


def test_oauth_state_expired_returns_none():
    user = pdb.create_user(role="athlete")
    _row, state = pdb.create_oauth_state(user["id"], expires_at=_past())
    assert pdb.consume_oauth_state(state) is None


def test_oauth_state_unknown_returns_none():
    assert pdb.consume_oauth_state("nope") is None
    assert pdb.consume_oauth_state("") is None


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
