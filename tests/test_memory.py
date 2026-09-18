"""Tests de la mémoire persistante du coach (faits, résumés, RAG)."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from domestique_ai.llm import memory
from domestique_ai.llm.conversations import append_message, new_session_id


def _fake_embed(texts, *, model=None, timeout_s=30.0):
    """Embedding déterministe 26-dim (fréquence de lettres) — pas de réseau."""
    out = []
    for text in texts:
        vec = [0.0] * 26
        for ch in text.lower():
            if "a" <= ch <= "z":
                vec[ord(ch) - ord("a")] += 1.0
        out.append(vec)
    return out


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(memory, "embed_texts_sync", _fake_embed)


def _use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "memory.db"))


# --------------------------------------------------------------------------- #
# Faits durables
# --------------------------------------------------------------------------- #


def test_remember_and_list_fact(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    res = memory.remember_fact("constraint", "Genou droit sensible au froid")
    assert res["deduplicated"] is False
    facts = memory.list_facts()
    assert len(facts) == 1
    assert facts[0]["content"] == "Genou droit sensible au froid"
    assert facts[0]["category"] == "constraint"
    assert facts[0]["active"] == 1


def test_remember_fact_dedup_updates(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    first = memory.remember_fact("preference", "Préfère rouler le matin")
    second = memory.remember_fact("preference", "Préfère rouler le matin")
    assert second["id"] == first["id"]
    assert second["deduplicated"] is True
    assert len(memory.list_facts()) == 1


def test_update_and_delete_fact(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    res = memory.remember_fact("goal", "Objectif : 250 W de FTP")
    updated = memory.update_fact(res["id"], content="Objectif : 260 W de FTP", pinned=True)
    assert updated is not None
    assert updated["content"] == "Objectif : 260 W de FTP"
    assert updated["pinned"] == 1
    assert memory.delete_fact(res["id"]) is True
    assert memory.list_facts() == []


def test_invalid_category_falls_back_to_personal(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    res = memory.remember_fact("nonsense", "Info diverse")
    assert res["category"] == "personal"


# --------------------------------------------------------------------------- #
# Résumés
# --------------------------------------------------------------------------- #


def test_summarize_session_persists_and_skips_when_unchanged(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory,
        "chat_structured_sync",
        lambda *a, **k: {
            "summary": "Le cycliste prépare une cyclosportive.",
            "topics": ["objectif"],
        },
    )
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "Je vise la cyclo en juin"})
    append_message(session, "assistant", {"role": "assistant", "content": "Bien noté."})

    first = memory.summarize_session(session)
    assert first is not None and first["updated"] is True
    assert first["topics"] == ["objectif"]

    # Aucun nouveau message → pas de régénération.
    second = memory.summarize_session(session)
    assert second is not None and second["updated"] is False

    stored = memory.get_session_summary(session)
    assert stored["summary"].startswith("Le cycliste")


def test_summarize_empty_session_returns_none(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    assert memory.summarize_session("nobody") is None


def test_should_summarize_threshold(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_SUMMARY_EVERY_MESSAGES", "4")
    session = new_session_id()
    for i in range(3):
        append_message(session, "user", {"role": "user", "content": f"m{i}"})
    assert memory.should_summarize(session) is False
    append_message(session, "user", {"role": "user", "content": "m3"})
    assert memory.should_summarize(session) is True


def test_extract_facts_from_session(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory,
        "chat_structured_sync",
        lambda *a, **k: {
            "facts": [
                {"category": "constraint", "content": "Douleur au genou droit"},
                {"category": "goal", "content": "Objectif cyclo en juin"},
            ]
        },
    )
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "Mon genou me gêne"})
    append_message(session, "assistant", {"role": "assistant", "content": "Noté."})

    stored = memory.extract_facts_from_session(session)
    assert len(stored) == 2
    contents = {f["content"] for f in memory.list_facts()}
    assert "Douleur au genou droit" in contents
    facts = memory.list_facts()
    assert all(f["source_session_id"] == session for f in facts)


# --------------------------------------------------------------------------- #
# RAG / bloc mémoire
# --------------------------------------------------------------------------- #


def test_get_relevant_memory_returns_closest(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    memory.index_message(1, "s1", "user", "j'ai mal au genou droit")
    memory.index_message(2, "s1", "user", "objectif de puissance ftp")
    hits = memory.get_relevant_memory("genou", k=2)
    assert hits
    assert "genou" in hits[0]["text"]


def test_index_message_is_idempotent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    memory.index_message(1, "s1", "user", "bonjour")
    memory.index_message(1, "s1", "user", "bonjour")
    hits = memory.get_relevant_memory("bonjour", k=5)
    assert len(hits) == 1


def test_build_memory_block_contains_facts(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    memory.remember_fact("constraint", "Épaule fragile")
    block = memory.build_memory_block("une question")
    assert "Épaule fragile" in block
    assert "MÉMOIRE PERSISTANTE" in block


def test_build_memory_block_empty_when_no_memory(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    assert memory.build_memory_block("q") == ""


# --------------------------------------------------------------------------- #
# Purge / finalisation
# --------------------------------------------------------------------------- #


def test_purge_session_keeps_facts(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "salut"})
    memory.index_message(1, session, "user", "salut")
    memory.remember_fact("constraint", "Genou fragile", source_session_id=session)

    memory.purge_session(session)

    # Vecteurs et résumé de la session supprimés…
    assert memory.get_relevant_memory("salut", k=5) == []
    # … mais le fait durable survit, source nullifiée.
    facts = memory.list_facts()
    assert len(facts) == 1
    assert facts[0]["source_session_id"] is None


def test_finalize_idle_sessions(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_IDLE_FINALIZE_MINUTES", "30")
    monkeypatch.setattr(
        memory,
        "chat_structured_sync",
        lambda *a, **k: {"summary": "Résumé.", "topics": [], "facts": []},
    )
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "a"})
    append_message(session, "assistant", {"role": "assistant", "content": "b"})

    # Backdate la dernière activité à 2 h.
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("UPDATE conversations SET created_at = ? WHERE session_id = ?", (old, session))
    conn.commit()
    conn.close()

    assert memory.finalize_idle_sessions() == 1
    assert memory.get_session_summary(session) is not None
    # Idempotent : plus rien à faire.
    assert memory.finalize_idle_sessions() == 0


def test_finalize_disabled_returns_zero(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_IDLE_FINALIZE_MINUTES", "0")
    assert memory.finalize_idle_sessions() == 0


def test_backfill_memory_sets_flag(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "bonjour à tous"})
    append_message(session, "assistant", {"role": "assistant", "content": "bonjour"})

    first = memory.backfill_memory()
    assert first["done"] is True
    assert first["messages"] == 2
    assert memory.get_relevant_memory("bonjour", k=5)

    second = memory.backfill_memory()
    assert second["done"] is False
