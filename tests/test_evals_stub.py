"""Tests du LLM scripté déterministe (``evals.lib.stub``)."""

from __future__ import annotations

import asyncio
from typing import Any

from evals.lib.models import StubTurn, ToolCallSpec
from evals.lib.stub import ScriptedLLM


def _drain(stub: ScriptedLLM, **kwargs: Any) -> list[dict[str, Any]]:
    async def _run() -> list[dict[str, Any]]:
        return [
            chunk async for chunk in stub.stream_chat([{"role": "user", "content": "x"}], **kwargs)
        ]

    return asyncio.run(_run())


def test_stream_chat_scripts_tool_call_then_content():
    stub = ScriptedLLM(
        turns=[
            StubTurn(tool_calls=[ToolCallSpec(name="get_profile")]),
            StubTurn(content="Ta FTP est de 250 W."),
        ]
    )
    first = _drain(stub, tools=[{"type": "function"}], think=True)
    assert first[-1]["tool_calls"][0]["function"]["name"] == "get_profile"
    assert first[-1]["done"] is True
    assert stub.calls[0]["think"] is True
    assert stub.calls[0]["tools"] == [{"type": "function"}]

    second = _drain(stub)
    assert second[0]["content"] == "Ta FTP est de 250 W."
    assert second[-1]["done"] is True
    assert second[-1]["tool_calls"] is None
    assert stub.exhausted is False


def test_stream_chat_marks_exhaustion():
    stub = ScriptedLLM(turns=[])
    chunks = _drain(stub)
    assert chunks[-1]["done"] is True
    assert chunks[-1]["tool_calls"] is None
    assert stub.exhausted is True


def test_system_messages_returns_first_call_context():
    stub = ScriptedLLM(turns=[StubTurn(content="ok")])

    async def _run() -> None:
        messages = [
            {"role": "system", "content": "Tu es un coach."},
            {"role": "user", "content": "Salut"},
        ]
        async for _ in stub.stream_chat(messages):
            pass

    asyncio.run(_run())
    assert stub.system_messages() == ["Tu es un coach."]


def test_chat_structured_pops_scripted_responses():
    stub = ScriptedLLM(structured=[{"workouts": []}, None])

    async def _call():
        first = await stub.chat_structured([], schema={"a": 1})
        second = await stub.chat_structured([], timeout_s=5.0)
        third = await stub.chat_structured([])
        return first, second, third

    first, second, third = asyncio.run(_call())
    assert first == {"workouts": []}
    assert second is None
    assert third is None
    assert stub.exhausted is True
    assert stub.structured_calls[0]["schema"] == {"a": 1}
    assert len(stub.structured_calls) == 3
