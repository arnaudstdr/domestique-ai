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


def test_extract_json_object_handles_fences_and_prefix():
    assert ollama_client.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert ollama_client.extract_json_object('Voici: {"a": 2} voilà') == {"a": 2}
    assert ollama_client.extract_json_object("pas de json") is None
    assert ollama_client.extract_json_object("") is None


def test_chat_structured_strips_markdown_fences(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, content='```json\n{"ok": true}\n```')
    result = asyncio.run(ollama_client.chat_structured([{"role": "user", "content": "x"}]))
    assert result == {"ok": True}
    # Parsé du premier coup : pas de repli de format.
    assert captured["format"] == "json"


def test_chat_structured_records_parse_error(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, content="pas du json")
    records: list[dict] = []
    monkeypatch.setattr(ollama_client, "record_llm_call", lambda **kw: records.append(kw))
    result = asyncio.run(
        ollama_client.chat_structured([{"role": "user", "content": "x"}], label="t")
    )
    assert result is None
    assert len(records) == 1
    assert records[0]["status"] == "error"
    assert records[0]["error_type"] == "parse"


def test_chat_structured_falls_back_to_json_format(monkeypatch):
    """Schéma non honoré → un unique repli en format="json"."""
    captured: list = []

    class _SequencedClient:
        def __init__(self, contents):
            self.contents = list(contents)

        async def chat(self, **kwargs):
            captured.append(kwargs.get("format"))
            content = self.contents.pop(0) if self.contents else ""
            return SimpleNamespace(
                message=SimpleNamespace(content=content),
                prompt_eval_count=10,
                eval_count=5,
                total_duration=1_000_000,
            )

    client = _SequencedClient(["pas du json", '{"ok": true}'])
    monkeypatch.setattr(ollama_client, "_async_client", lambda: client)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    result = asyncio.run(
        ollama_client.chat_structured([{"role": "user", "content": "x"}], schema=schema)
    )
    assert result == {"ok": True}
    assert captured == [schema, "json"]
