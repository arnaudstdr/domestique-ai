"""Tests HTTP des endpoints mémoire du coach (`/api/coach/memory`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db


def _fake_embed(texts, *, model=None, timeout_s=30.0):
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
    db = tmp_path / "memory_api.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    from domestique_ai.llm import memory

    monkeypatch.setattr(memory, "embed_texts_sync", _fake_embed)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def test_memory_empty(client: TestClient) -> None:
    r = client.get("/api/coach/memory")
    assert r.status_code == 200
    assert r.json() == []


def test_memory_create_update_delete(client: TestClient) -> None:
    r = client.post(
        "/api/coach/memory",
        json={"category": "constraint", "content": "Genou fragile", "pinned": True},
    )
    assert r.status_code == 201
    fact = r.json()
    assert fact["content"] == "Genou fragile"
    assert fact["pinned"] is True

    listed = client.get("/api/coach/memory").json()
    assert len(listed) == 1

    upd = client.put(
        f"/api/coach/memory/{fact['id']}",
        json={"content": "Genou très fragile", "pinned": False},
    )
    assert upd.status_code == 200
    assert upd.json()["content"] == "Genou très fragile"
    assert upd.json()["pinned"] is False

    de = client.delete(f"/api/coach/memory/{fact['id']}")
    assert de.status_code == 204
    assert client.get("/api/coach/memory").json() == []


def test_memory_create_rejects_empty(client: TestClient) -> None:
    r = client.post("/api/coach/memory", json={"category": "personal", "content": "   "})
    assert r.status_code == 400


def test_memory_update_unknown_404(client: TestClient) -> None:
    r = client.put("/api/coach/memory/9999", json={"content": "x"})
    assert r.status_code == 404
