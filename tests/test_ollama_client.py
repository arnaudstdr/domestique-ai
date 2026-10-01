"""Tests du wrapper Ollama — passage du schéma JSON au SDK (``format``)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from domestique_ai.llm import ollama_client


class _FakeAsyncClient:
    def __init__(self, captured: dict, content: str = '{"ok": true}') -> None:
        self.captured = captured
        self.content = content

    async def chat(self, **kwargs):
        self.captured.update(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(content=self.content),
            prompt_eval_count=10,
            eval_count=5,
            total_duration=1_000_000,
        )


def _patch_client(monkeypatch, captured: dict, content: str = '{"ok": true}') -> None:
    monkeypatch.setattr(ollama_client, "_async_client", lambda: _FakeAsyncClient(captured, content))


def test_chat_structured_passes_schema_as_format(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured)
    schema = {"type": "object", "properties": {"reason": {"type": "string"}}}
    result = asyncio.run(
        ollama_client.chat_structured([{"role": "user", "content": "x"}], schema=schema, label="t")
    )
    assert result == {"ok": True}
    assert captured["format"] == schema


def test_chat_structured_defaults_to_json_format(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured)
    asyncio.run(ollama_client.chat_structured([{"role": "user", "content": "x"}]))
    assert captured["format"] == "json"


def test_chat_structured_sync_forwards_schema(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured)
    schema = {"type": "object"}
    result = ollama_client.chat_structured_sync([{"role": "user", "content": "x"}], schema=schema)
    assert result == {"ok": True}
    assert captured["format"] == schema


def test_chat_structured_returns_none_on_invalid_json(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, content="pas du json")
    result = asyncio.run(ollama_client.chat_structured([{"role": "user", "content": "x"}]))
    assert result is None
