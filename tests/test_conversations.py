"""Tests pour la persistance des conversations coach."""

from __future__ import annotations

from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
)


def test_session_id_is_unique():
    assert new_session_id() != new_session_id()


def test_append_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "conv.db"))
    session = new_session_id()

    append_message(session, "user", {"role": "user", "content": "Hello"})
    append_message(session, "assistant", {"role": "assistant", "content": "Hi", "thinking": "ok"})
    append_message(session, "tool", {"role": "tool", "name": "ping", "content": "pong"})

    messages = load_session(session)
    assert len(messages) == 3
    assert messages[0]["content"] == "Hello"
    assert messages[1]["thinking"] == "ok"
    assert messages[2]["name"] == "ping"


def test_list_sessions_returns_recent_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "conv.db"))
    s1 = new_session_id()
    s2 = new_session_id()
    append_message(s1, "user", {"role": "user", "content": "first"})
    append_message(s2, "user", {"role": "user", "content": "second"})

    sessions = list_sessions()
    assert len(sessions) == 2
    # most recent first (insertion order in same second → second one first)
    assert sessions[0]["session_id"] == s2
    assert sessions[0]["preview"] == "second"
    assert sessions[1]["session_id"] == s1


def test_load_session_unknown_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "conv.db"))
    # Force la création de la table
    append_message(new_session_id(), "user", {"role": "user", "content": "x"})
    assert load_session("does-not-exist") == []


def test_delete_session_removes_only_targeted(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "conv.db"))
    s1 = new_session_id()
    s2 = new_session_id()
    append_message(s1, "user", {"role": "user", "content": "a"})
    append_message(s1, "assistant", {"role": "assistant", "content": "b"})
    append_message(s2, "user", {"role": "user", "content": "c"})

    deleted = delete_session(s1)
    assert deleted == 2
    assert load_session(s1) == []
    assert len(load_session(s2)) == 1
    assert {s["session_id"] for s in list_sessions()} == {s2}


def test_delete_session_unknown_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "conv.db"))
    append_message(new_session_id(), "user", {"role": "user", "content": "x"})
    assert delete_session("does-not-exist") == 0
