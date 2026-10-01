"""Tests du fil unique du coach : pagination, rotation de session, recherche."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db
from domestique_ai.llm.conversations import (
    append_message,
    current_or_new_session,
    load_thread_page,
)


def _seed(db: Path, session_id: str, turns: list[tuple[str, str]]) -> list[int]:
    return [
        append_message(session_id, role, {"role": role, "content": content}, db_path=db)
        for role, content in turns
    ]


def _age_messages(db: Path, minutes: int) -> None:
    stale = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE conversations SET created_at = ?", (stale,))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# load_thread_page
# --------------------------------------------------------------------------- #


def test_thread_latest_merges_sessions_ascending(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    a = _seed(db, "s1", [("user", "a1"), ("assistant", "a2")])
    b = _seed(db, "s2", [("user", "b1"), ("assistant", "b2")])

    page = load_thread_page(limit=3, db_path=db)

    assert [m["id"] for m in page["messages"]] == [a[1], b[0], b[1]]
    assert page["has_more_before"] is True
    assert page["has_more_after"] is False
    assert page["messages"][0]["session_id"] == "s1"


def test_thread_before_and_after_pagination(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    a = _seed(db, "s1", [("user", "a1"), ("assistant", "a2")])
    b = _seed(db, "s2", [("user", "b1"), ("assistant", "b2")])

    older = load_thread_page(limit=3, before=b[0], db_path=db)
    assert [m["id"] for m in older["messages"]] == [a[0], a[1]]
    assert older["has_more_before"] is False
    assert older["has_more_after"] is True

    newer = load_thread_page(limit=3, after=a[1], db_path=db)
    assert [m["id"] for m in newer["messages"]] == [b[0], b[1]]
    assert newer["has_more_before"] is True
    assert newer["has_more_after"] is False


def test_thread_anchor_returns_centered_window(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    _seed(db, "s1", [("user", "a1"), ("assistant", "a2")])
    b = _seed(db, "s2", [("user", "b1"), ("assistant", "b2")])

    page = load_thread_page(limit=2, anchor=b[0], db_path=db)

    assert [m["id"] for m in page["messages"]] == [b[0], b[1]]
    assert page["has_more_before"] is True


def test_thread_skips_empty_user_messages(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    append_message("s1", "user", {"role": "user", "content": "   "}, db_path=db)
    append_message("s1", "assistant", {"role": "assistant", "content": "ok"}, db_path=db)

    page = load_thread_page(db_path=db)

    assert [m["role"] for m in page["messages"]] == ["assistant"]


# --------------------------------------------------------------------------- #
# current_or_new_session
# --------------------------------------------------------------------------- #


def test_current_session_reuses_fresh_window(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    _seed(db, "s1", [("user", "salut")])

    result = current_or_new_session(45, db_path=db)

    assert result == {"session_id": "s1", "rotated": False, "previous_session_id": None}


def test_current_session_rotates_when_stale(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    _seed(db, "old", [("user", "salut")])
    _age_messages(db, minutes=120)

    result = current_or_new_session(45, db_path=db)

    assert result["rotated"] is True
    assert result["previous_session_id"] == "old"
    assert result["session_id"] != "old"


def test_current_session_on_empty_db_opens_new(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)

    result = current_or_new_session(45, db_path=db)

    assert result["rotated"] is True
    assert result["previous_session_id"] is None


def test_current_session_disabled_keeps_single_thread(tmp_path: Path) -> None:
    db = tmp_path / "thread.db"
    init_db(db)
    _seed(db, "s1", [("user", "salut")])
    _age_messages(db, minutes=120)

    result = current_or_new_session(0, db_path=db)

    assert result == {"session_id": "s1", "rotated": False, "previous_session_id": None}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _fake_embed(texts, *, model=None, timeout_s=30.0, label=None):
    out = []
    for text in texts:
        vec = [0.0] * 26
        for ch in text.lower():
            if "a" <= ch <= "z":
                vec[ord(ch) - ord("a")] += 1.0
        out.append(vec)
    return out


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "coach_thread.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    from domestique_ai.llm import memory

    monkeypatch.setattr(memory, "embed_texts_sync", _fake_embed)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def test_thread_endpoint_pagination(client: TestClient) -> None:
    from domestique_ai.config import get_db_path

    db = get_db_path()
    a = append_message("s1", "user", {"role": "user", "content": "a"}, db_path=db)
    b = append_message("s1", "assistant", {"role": "assistant", "content": "b"}, db_path=db)

    latest = client.get("/api/coach/messages", params={"limit": 1}).json()
    assert [m["id"] for m in latest["messages"]] == [b]
    assert latest["has_more_before"] is True

    older = client.get("/api/coach/messages", params={"before": b, "limit": 10}).json()
    assert [m["id"] for m in older["messages"]] == [a]


def test_search_endpoint_returns_message_id(client: TestClient) -> None:
    from domestique_ai.config import get_db_path
    from domestique_ai.llm import memory

    db = get_db_path()
    mid = append_message("s1", "user", {"role": "user", "content": "genou douleur"}, db_path=db)
    memory.index_message(mid, "s1", "user", "genou douleur")

    r = client.get("/api/coach/search", params={"q": "genou douleur"})

    assert r.status_code == 200
    hits = r.json()
    assert hits
    assert hits[0]["message_id"] == mid
    assert hits[0]["source_type"] == "message"


def test_chat_without_session_id_persists_thread(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domestique_ai.api.routers import coach as coach_router
    from domestique_ai.config import get_db_path

    async def fake_run(user_message, history=None, *, ctx=None):
        yield {"type": "token", "value": "ok"}
        yield {"type": "final", "content": "ok", "thinking": None, "tool_trace": []}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(coach_router, "run_turn_stream", fake_run)
    monkeypatch.setattr(coach_router, "_update_memory_safely", noop)

    r = client.post("/api/coach/chat", json={"message": "salut"})

    assert r.status_code == 200
    assert "session_id" in r.text
    page = load_thread_page(db_path=get_db_path())
    assert [m["role"] for m in page["messages"]] == ["user", "assistant"]


def test_chat_rotates_stale_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from domestique_ai.api.routers import coach as coach_router
    from domestique_ai.config import get_db_path

    db = get_db_path()
    _seed(db, "old", [("user", "premier échange"), ("assistant", "réponse")])
    _age_messages(db, minutes=120)

    async def fake_run(user_message, history=None, *, ctx=None):
        yield {"type": "final", "content": "nouveau", "thinking": None, "tool_trace": []}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(coach_router, "run_turn_stream", fake_run)
    monkeypatch.setattr(coach_router, "_update_memory_safely", noop)
    monkeypatch.setattr(coach_router, "_finalize_session_safely", noop)
    monkeypatch.setattr(coach_router, "get_session_idle_finalize_minutes", lambda: 45)

    r = client.post("/api/coach/chat", json={"message": "je reviens"})

    assert r.status_code == 200
    sessions = {m["session_id"] for m in load_thread_page(limit=50, db_path=db)["messages"]}
    assert len(sessions) == 2
    assert "old" in sessions
