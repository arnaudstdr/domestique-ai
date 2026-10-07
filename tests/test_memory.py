"""Tests de la mémoire persistante du coach (faits, résumés, RAG)."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from domestique_ai.llm import memory
from domestique_ai.llm.conversations import append_message, new_session_id


def _fake_embed(texts, *, model=None, timeout_s=30.0, label=None):
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
    # Le cache d'embeddings de requête est global au module : on le vide entre
    # les tests pour qu'un fake ne fuite pas dans le suivant.
    memory.clear_embedding_cache()
    yield
    memory.clear_embedding_cache()


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


def test_summarize_session_is_incremental(tmp_path, monkeypatch):
    """Le 2ᵉ résumé ne renvoie que les nouveaux messages + le résumé précédent."""
    _use_tmp_db(tmp_path, monkeypatch)
    prompts: list[str] = []

    def fake_llm(messages, **kwargs):
        prompts.append(messages[0]["content"])
        return {"summary": f"Résumé {len(prompts)}.", "topics": []}

    monkeypatch.setattr(memory, "chat_structured_sync", fake_llm)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "Ancien message secret"})
    append_message(session, "assistant", {"role": "assistant", "content": "Ancienne réponse"})

    first = memory.summarize_session(session)
    assert first is not None and first["updated"] is True
    assert "Ancien message secret" in prompts[0]
    assert "Résumé précédent" not in prompts[0]

    append_message(session, "user", {"role": "user", "content": "Nouveau message frais"})
    append_message(session, "assistant", {"role": "assistant", "content": "Nouvelle réponse"})

    second = memory.summarize_session(session)
    assert second is not None and second["updated"] is True
    assert "Nouveau message frais" in prompts[1]
    assert "Ancien message secret" not in prompts[1]
    assert "Résumé 1." in prompts[1]


def test_finalize_is_incremental(tmp_path, monkeypatch):
    """La finalisation ne renvoie que les messages postérieurs au résumé roulant."""
    _use_tmp_db(tmp_path, monkeypatch)
    prompts: list[str] = []

    def fake_llm(messages, **kwargs):
        prompts.append(messages[0]["content"])
        return {"summary": f"Résumé {len(prompts)}.", "topics": [], "facts": []}

    monkeypatch.setattr(memory, "chat_structured_sync", fake_llm)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "Vieille info"})
    memory.summarize_session(session)

    append_message(session, "user", {"role": "user", "content": "Info récente"})
    result = memory.summarize_and_extract_facts(session)
    assert result is not None and result["updated"] is True
    assert "Info récente" in prompts[1]
    assert "Vieille info" not in prompts[1]
    assert "Résumé 1." in prompts[1]


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


def _counting_embed(calls: dict[str, int]):
    def _embed(texts, *, model=None, timeout_s=30.0, label=None):
        calls["n"] += 1
        return _fake_embed(texts)

    return _embed


def test_query_embedding_is_cached(tmp_path, monkeypatch):
    """Deux recherches identiques ne paient qu'un seul embedding."""
    _use_tmp_db(tmp_path, monkeypatch)
    memory.index_message(1, "s1", "user", "j'ai mal au genou droit")
    calls = {"n": 0}
    monkeypatch.setattr(memory, "embed_texts_sync", _counting_embed(calls))
    memory.get_relevant_memory("genou", k=2)
    memory.get_relevant_memory("genou", k=2)
    assert calls["n"] == 1


def test_get_relevant_memory_skips_embedding_when_empty(tmp_path, monkeypatch):
    """Aucun vecteur indexé → aucun appel d'embedding (athlète sans historique)."""
    _use_tmp_db(tmp_path, monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(memory, "embed_texts_sync", _counting_embed(calls))
    assert memory.get_relevant_memory("genou", k=2) == []
    assert calls["n"] == 0


def test_extract_facts_batches_embeddings(tmp_path, monkeypatch):
    """Tous les faits d'une extraction partent dans UN appel d'embeddings."""
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory,
        "chat_structured_sync",
        lambda *a, **k: {
            "facts": [
                {"category": "goal", "content": "Objectif cyclo en juin"},
                {"category": "preference", "content": "Préfère rouler le matin"},
                {"category": "constraint", "content": "Genou droit fragile"},
            ]
        },
    )
    calls = {"n": 0}
    monkeypatch.setattr(memory, "embed_texts_sync", _counting_embed(calls))
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "x"})
    stored = memory.extract_facts_from_session(session)
    assert len(stored) == 3
    assert calls["n"] == 1


def test_build_memory_block_contains_facts(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    memory.remember_fact("constraint", "Épaule fragile")
    block = memory.build_memory_block("une question")
    assert "Épaule fragile" in block
    assert "MÉMOIRE PERSISTANTE" in block


def test_build_memory_block_empty_when_no_memory(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    assert memory.build_memory_block("q") == ""


def test_build_memory_block_caps_facts_and_keeps_pinned(tmp_path, monkeypatch):
    """Top 20 faits max, l'épinglé est toujours injecté."""
    import re

    _use_tmp_db(tmp_path, monkeypatch)
    for i in range(25):
        letter = chr(ord("a") + i)
        memory.remember_fact("preference", letter.upper() * 20)
    # Épingle un fait dont la lettre n'est pas dans la question (mal classé).
    pinned = next(f for f in memory.list_facts() if f["content"].startswith("Y"))
    memory.update_fact(pinned["id"], pinned=True)

    block = memory.build_memory_block("question")
    injected = re.findall(r"([A-Z])\1{19}", block)
    assert len(injected) == 20
    assert "Y" * 20 in block


def test_build_memory_block_includes_level(tmp_path, monkeypatch):
    """Le niveau de l'athlète est injecté à chaque tour (coaching renforcé)."""
    import types

    _use_tmp_db(tmp_path, monkeypatch)
    ctx = types.SimpleNamespace(level="racer", db_path=None)
    block = memory.build_memory_block("q", ctx=ctx)
    assert "niveau compétiteur (en activité)" in block
    assert "MÉMOIRE PERSISTANTE" in block


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


def test_finalize_uses_single_llm_call(tmp_path, monkeypatch):
    """La finalisation fait résumé + faits en UN seul appel LLM."""
    _use_tmp_db(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_llm(*args, **kwargs):
        calls["n"] += 1
        return {
            "summary": "Résumé final.",
            "topics": ["objectif"],
            "facts": [{"category": "goal", "content": "Objectif cyclo en juin"}],
        }

    monkeypatch.setattr(memory, "chat_structured_sync", fake_llm)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "a"})
    append_message(session, "assistant", {"role": "assistant", "content": "b"})

    result = memory.summarize_and_extract_facts(session)
    assert result is not None
    assert result["updated"] is True
    assert calls["n"] == 1
    assert len(result["facts"]) == 1
    assert memory.get_session_summary(session)["summary"] == "Résumé final."


def _backdate_attempts(tmp_path):
    """Recule ``last_attempt_at`` pour contourner le backoff dans les tests."""
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("UPDATE session_finalize_state SET last_attempt_at = ?", (old,))
    conn.commit()
    conn.close()


def test_finalize_attempts_are_bounded_and_backed_off(tmp_path, monkeypatch):
    """Une finalisation qui échoue n'est pas resoumise en boucle indéfiniment."""
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_IDLE_FINALIZE_MINUTES", "30")
    monkeypatch.setenv("SESSION_FINALIZE_MAX_ATTEMPTS", "2")
    calls = {"n": 0}

    def failing_llm(*args, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(memory, "chat_structured_sync", failing_llm)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "a"})
    append_message(session, "assistant", {"role": "assistant", "content": "b"})
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("UPDATE conversations SET created_at = ? WHERE session_id = ?", (old, session))
    conn.commit()
    conn.close()

    # 1ᵉʳ essai → 1 appel, échec borné.
    assert memory.finalize_idle_sessions() == 0
    assert calls["n"] == 1
    # Backoff : un passage immédiat ne rappelle pas.
    assert memory.finalize_idle_sessions() == 0
    assert calls["n"] == 1

    # Après le backoff → 2ᵉ essai (plafond non encore atteint).
    _backdate_attempts(tmp_path)
    assert memory.finalize_idle_sessions() == 0
    assert calls["n"] == 2

    # Plafond atteint, aucun nouveau message → plus aucun appel (la fuite).
    _backdate_attempts(tmp_path)
    assert memory.finalize_idle_sessions() == 0
    assert calls["n"] == 2

    # Un nouveau message rouvre le droit à une tentative.
    append_message(session, "user", {"role": "user", "content": "c"})
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("UPDATE conversations SET created_at = ? WHERE session_id = ?", (old, session))
    conn.commit()
    conn.close()
    _backdate_attempts(tmp_path)
    assert memory.finalize_idle_sessions() == 0
    assert calls["n"] == 3


def test_finalize_respects_max_per_run(tmp_path, monkeypatch):
    """Le cap par passage borne le nombre de sessions traitées d'un run."""
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_IDLE_FINALIZE_MINUTES", "30")
    monkeypatch.setenv("SESSION_FINALIZE_MAX_PER_RUN", "1")
    calls = {"n": 0}

    def fake_llm(*args, **kwargs):
        calls["n"] += 1
        return {"summary": "Résumé.", "topics": [], "facts": []}

    monkeypatch.setattr(memory, "chat_structured_sync", fake_llm)
    for _ in range(3):
        session = new_session_id()
        append_message(session, "user", {"role": "user", "content": "a"})
        append_message(session, "assistant", {"role": "assistant", "content": "b"})
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("UPDATE conversations SET created_at = ?", (old,))
    conn.commit()
    conn.close()

    assert memory.finalize_idle_sessions() == 1
    assert calls["n"] == 1


def test_finalize_success_clears_attempt_state(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory,
        "chat_structured_sync",
        lambda *a, **k: {"summary": "Résumé.", "topics": [], "facts": []},
    )
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "a"})
    append_message(session, "assistant", {"role": "assistant", "content": "b"})
    result = memory.summarize_and_extract_facts(session)
    assert result is not None and result["updated"] is True
    assert memory._get_finalize_state(session) is None


def test_summarize_session_attempts_are_bounded(tmp_path, monkeypatch):
    """Le résumé roulant (chat) est borné lui aussi."""
    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_FINALIZE_MAX_ATTEMPTS", "1")
    calls = {"n": 0}

    def failing_llm(*args, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(memory, "chat_structured_sync", failing_llm)
    session = new_session_id()
    append_message(session, "user", {"role": "user", "content": "a"})
    append_message(session, "assistant", {"role": "assistant", "content": "b"})

    assert memory.summarize_session(session) is None
    assert calls["n"] == 1
    # Plafond atteint, pas de nouveau message → aucun appel supplémentaire.
    blocked = memory.summarize_session(session)
    assert blocked is not None and blocked["updated"] is False
    assert calls["n"] == 1


def test_transcript_caps_total_chars():
    """Le transcript est borné globalement, en gardant les messages récents."""
    messages = [{"id": i, "role": "user", "payload": {"content": "x" * 500}} for i in range(200)]
    text = memory._transcript(messages, max_total_chars=2000)
    assert text.count("[CYCLISTE]") == 3
    assert len(text) <= 2000


def test_transcript_since_id_filters_old_messages():
    messages = [
        {"id": 1, "role": "user", "payload": {"content": "vieux"}},
        {"id": 2, "role": "assistant", "payload": {"content": "vieux aussi"}},
        {"id": 3, "role": "user", "payload": {"content": "récent"}},
    ]
    text = memory._transcript(messages, since_id=2)
    assert "récent" in text
    assert "vieux" not in text


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
