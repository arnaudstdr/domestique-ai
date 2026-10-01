"""Tests de l'observabilité LLM (``llm/usage.py`` + table ``llm_calls``)."""

from __future__ import annotations

from domestique_ai import platform_db
from domestique_ai.llm import usage


def test_record_llm_call_persists_metrics():
    usage.record_llm_call(
        label=usage.COACH_CHAT,
        entrypoint="stream_chat",
        model="gemma4:31b-cloud",
        prompt_tokens=100,
        cached_tokens=40,
        completion_tokens=25,
        total_duration_ms=1234.5,
        load_duration_ms=100.0,
        eval_duration_ms=900.0,
        tools_count=2,
        actor="abc123",
    )
    rows = platform_db.fetch_llm_calls()
    assert len(rows) == 1
    r = rows[0]
    assert r["label"] == "coach_chat"
    assert r["model"] == "gemma4:31b-cloud"
    assert r["prompt_tokens"] == 100
    assert r["cached_tokens"] == 40
    assert r["completion_tokens"] == 25
    assert r["tools_count"] == 2
    assert r["actor_public_id"] == "abc123"
    assert r["status"] == "ok"


def test_record_llm_call_uses_contextvar_actor():
    with usage.llm_attribution("pid-xyz"):
        assert usage.current_llm_actor() == "pid-xyz"
        usage.record_llm_call(
            label=usage.EMBED_QUERY,
            entrypoint="embed_texts",
            model="nomic-embed-text",
        )
    # Hors contexte → plus d'acteur.
    assert usage.current_llm_actor() is None
    rows = platform_db.fetch_llm_calls()
    assert rows[0]["actor_public_id"] == "pid-xyz"


def test_record_llm_call_never_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(platform_db, "insert_llm_call", _boom)
    # Ne doit pas propager l'exception.
    usage.record_llm_call(label="x", entrypoint="y", model="z")


def test_error_call_recorded_with_status():
    usage.record_llm_call(
        label=usage.DECISION_REASON,
        entrypoint="chat_structured",
        model="gemma4:31b-cloud",
        status="error",
        error_type="timeout",
    )
    r = platform_db.fetch_llm_calls()[0]
    assert r["status"] == "error"
    assert r["error_type"] == "timeout"


def test_label_human_fallback():
    assert usage.label_human(usage.COACH_CHAT) == "Chat coach"
    assert usage.label_human("unknown_label") == "unknown_label"
    assert usage.label_human(None) == "—"


def test_as_int_and_ns_to_ms():
    assert usage.as_int(None) is None
    assert usage.as_int(5) == 5
    assert usage.as_int("7") == 7
    assert usage.ns_to_ms(None) is None
    assert usage.ns_to_ms(1_500_000) == 1.5
