"""Mémoire persistante du coach LLM.

Trois étages, stockés dans le même SQLite que l'athlète (``ctx.db_path``) :

- **faits durables** (``coach_memory``) : préférences, contraintes, objectifs,
  accords — toujours injectés dans le prompt système ;
- **résumés épisodiques** (``session_summaries``) : un résumé par session,
  rafraîchi tous les ``SESSION_SUMMARY_EVERY_MESSAGES`` messages (roulant) ou à
  la finalisation (inactivité ``SESSION_IDLE_FINALIZE_MINUTES`` / appel
  explicite) ;
- **vecteurs** (``memory_vectors``) : index de retrieval unifié (messages,
  résumés, faits) pour la recherche sémantique brute-force.

Tout est **best-effort** : une panne d'Ollama (embeddings ou résumé) ne doit
jamais bloquer le chat — les fonctions dégradent en silence et retournent une
valeur vide/neutre.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import (
    get_db_path,
    get_session_idle_finalize_minutes,
    get_session_summary_every_messages,
)
from domestique_ai.ingestion.db import init_db
from domestique_ai.llm.ollama_client import chat_structured_sync, embed_texts_sync

MEMORY_CATEGORIES = ("preference", "constraint", "goal", "agreement", "personal")

_CATEGORY_LABELS = {
    "preference": "Préférences",
    "constraint": "Contraintes",
    "goal": "Objectifs",
    "agreement": "Accords",
    "personal": "Perso",
}

# Budgets d'injection (bornent le prompt sans étouffer le contexte courant).
_MAX_FACTS = 40
_MAX_SUMMARIES = 5
_MAX_RAG_HITS = 4
_MAX_FACT_CHARS = 300
_MAX_SUMMARY_CHARS = 700
_MAX_RAG_CHARS = 400
_FACT_DEDUP_THRESHOLD = 0.9


def _resolve_db_path(db_path: Path | None, ctx: AthleteContext | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if ctx is not None:
        return ctx.db_path
    return get_db_path()


def _connect(db_path: Path | None = None, ctx: AthleteContext | None = None) -> sqlite3.Connection:
    path = _resolve_db_path(db_path, ctx)
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


# --------------------------------------------------------------------------- #
# Vecteurs
# --------------------------------------------------------------------------- #


def _pack(vec: list[float]) -> bytes:
    import numpy as np

    return np.asarray(vec, dtype="<f4").tobytes()


def _cosine(query: list[float], rows: list[sqlite3.Row], k: int) -> list[tuple[float, sqlite3.Row]]:
    """Top-k cosinus entre ``query`` et les embeddings BLOB de ``rows``.

    Les vecteurs de dimension différente de la requête (changement de modèle
    d'embedding) sont ignorés plutôt que de faire planter le retrieval.
    """
    import numpy as np

    q = np.asarray(query, dtype="<f4")
    qn = float(np.linalg.norm(q)) or 1.0
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        blob = row["embedding"]
        if not blob:
            continue
        v = np.frombuffer(blob, dtype="<f4")
        if v.shape != q.shape:
            continue
        denom = qn * (float(np.linalg.norm(v)) or 1.0)
        scored.append((float(np.dot(q, v)) / denom, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:k]


# --------------------------------------------------------------------------- #
# Faits durables
# --------------------------------------------------------------------------- #


def remember_fact(
    category: str,
    content: str,
    *,
    ctx: AthleteContext | None = None,
    source_session_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Enregistre (ou met à jour si quasi-duplicata) un fait durable.

    La dédup compare l'embedding du nouveau contenu aux faits actifs : au-delà
    de ``_FACT_DEDUP_THRESHOLD`` de similarité, on met à jour la ligne existante
    plutôt que d'en créer une nouvelle.
    """
    content = (content or "").strip()
    if not content:
        return {"error": "content vide"}
    if category not in MEMORY_CATEGORIES:
        category = "personal"

    embedding = embed_texts_sync([content])
    vec = embedding[0] if embedding else None
    now = _now()

    conn = _connect(db_path, ctx)
    try:
        existing = conn.execute(
            "SELECT id, content, embedding FROM coach_memory WHERE active = 1"
        ).fetchall()
        match_id: int | None = None
        if vec and existing:
            hits = _cosine(vec, existing, 1)
            if hits and hits[0][0] >= _FACT_DEDUP_THRESHOLD:
                match_id = int(hits[0][1]["id"])

        if match_id is not None:
            conn.execute(
                "UPDATE coach_memory SET category = ?, content = ?, embedding = ?, "
                "updated_at = ? WHERE id = ?",
                (category, content, _pack(vec) if vec else None, now, match_id),
            )
            fact_id = match_id
            deduplicated = True
        else:
            cursor = conn.execute(
                "INSERT INTO coach_memory "
                "(category, content, embedding, source_session_id, pinned, active, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, 0, 1, ?, ?)",
                (
                    category,
                    content,
                    _pack(vec) if vec else None,
                    source_session_id,
                    now,
                    now,
                ),
            )
            fact_id = int(cursor.lastrowid)
            deduplicated = False

        conn.execute(
            "DELETE FROM memory_vectors WHERE source_type = 'fact' AND ref_id = ?",
            (fact_id,),
        )
        if vec:
            conn.execute(
                "INSERT INTO memory_vectors "
                "(source_type, ref_id, session_id, text, embedding, created_at) "
                "VALUES ('fact', ?, NULL, ?, ?, ?)",
                (fact_id, content, _pack(vec), now),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": fact_id,
        "category": category,
        "content": content,
        "deduplicated": deduplicated,
    }


def list_facts(
    *,
    active_only: bool = True,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Liste les faits mémorisés, épinglés d'abord puis plus récents d'abord."""
    conn = _connect(db_path, ctx)
    try:
        where = "WHERE active = 1" if active_only else ""
        rows = conn.execute(
            f"SELECT id, category, content, source_session_id, pinned, active, "
            f"created_at, updated_at FROM coach_memory {where} "
            f"ORDER BY pinned DESC, updated_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_fact(
    fact_id: int,
    *,
    content: str | None = None,
    category: str | None = None,
    pinned: bool | None = None,
    active: bool | None = None,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Met à jour un fait (contenu, catégorie, épingle, activation).

    Ré-embedde et ré-indexe si le contenu change. Retourne le fait à jour, ou
    ``None`` si l'id est inconnu.
    """
    conn = _connect(db_path, ctx)
    try:
        row = conn.execute(
            "SELECT id, content FROM coach_memory WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None:
            return None

        now = _now()
        new_content = content.strip() if content is not None else None
        vec = None
        if new_content:
            embedded = embed_texts_sync([new_content])
            vec = embedded[0] if embedded else None

        if new_content is not None:
            conn.execute(
                "UPDATE coach_memory SET content = ?, embedding = ?, updated_at = ? WHERE id = ?",
                (new_content, _pack(vec) if vec else None, now, fact_id),
            )
        if category is not None and category in MEMORY_CATEGORIES:
            conn.execute(
                "UPDATE coach_memory SET category = ?, updated_at = ? WHERE id = ?",
                (category, now, fact_id),
            )
        if pinned is not None:
            conn.execute(
                "UPDATE coach_memory SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, now, fact_id),
            )
        if active is not None:
            conn.execute(
                "UPDATE coach_memory SET active = ?, updated_at = ? WHERE id = ?",
                (1 if active else 0, now, fact_id),
            )

        if new_content is not None:
            conn.execute(
                "DELETE FROM memory_vectors WHERE source_type = 'fact' AND ref_id = ?",
                (fact_id,),
            )
            if vec:
                conn.execute(
                    "INSERT INTO memory_vectors "
                    "(source_type, ref_id, session_id, text, embedding, created_at) "
                    "VALUES ('fact', ?, NULL, ?, ?, ?)",
                    (fact_id, new_content, _pack(vec), now),
                )
        conn.commit()
        out = conn.execute(
            "SELECT id, category, content, source_session_id, pinned, active, "
            "created_at, updated_at FROM coach_memory WHERE id = ?",
            (fact_id,),
        ).fetchone()
        return dict(out) if out else None
    finally:
        conn.close()


def delete_fact(
    fact_id: int,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> bool:
    """Supprime un fait et son vecteur associé."""
    conn = _connect(db_path, ctx)
    try:
        conn.execute("DELETE FROM coach_memory WHERE id = ?", (fact_id,))
        conn.execute(
            "DELETE FROM memory_vectors WHERE source_type = 'fact' AND ref_id = ?", (fact_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Session : messages + résumés
# --------------------------------------------------------------------------- #


def _load_messages_with_ids(
    session_id: str, db_path: Path | None = None, ctx: AthleteContext | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path, ctx)
    try:
        rows = conn.execute(
            "SELECT id, role, payload FROM conversations WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        out.append({"id": int(row["id"]), "role": row["role"], "payload": payload})
    return out


def _transcript(messages: list[dict[str, Any]], *, limit_chars: int = 500) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("payload") or {}).get("content") or ""
        content = content.strip()
        if role not in ("user", "assistant") or not content:
            continue
        label = "CYCLISTE" if role == "user" else "COACH"
        lines.append(f"[{label}] {content[:limit_chars]}")
    return "\n".join(lines)


def get_session_summary(
    session_id: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    conn = _connect(db_path, ctx)
    try:
        row = conn.execute(
            "SELECT session_id, summary, topics, message_count, "
            "last_summarized_message_id, created_at, updated_at "
            "FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    data = dict(row)
    try:
        data["topics"] = json.loads(data["topics"]) if data.get("topics") else []
    except (json.JSONDecodeError, TypeError):
        data["topics"] = []
    return data


def summarize_session(
    session_id: str,
    *,
    final: bool = False,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Résume une session et persiste le résumé (best-effort).

    Ne régénère rien s'il n'y a pas de nouveaux messages depuis le dernier
    résumé (``last_summarized_message_id``) : ``updated`` vaut alors ``False``
    et le résumé existant est renvoyé tel quel. Retourne ``None`` si la session
    est vide ou si la génération échoue.
    """
    messages = _load_messages_with_ids(session_id, db_path=db_path, ctx=ctx)
    if not messages:
        return None

    existing = get_session_summary(session_id, ctx=ctx, db_path=db_path)
    last_id = (existing or {}).get("last_summarized_message_id") or 0
    max_id = max(m["id"] for m in messages)
    if existing and max_id <= int(last_id):
        result = dict(existing)
        result["updated"] = False
        return result

    transcript = _transcript(messages)
    if not transcript:
        return None

    prompt = (
        "Tu résumes, pour un coach d'endurance, une conversation entre un "
        "cycliste et son coach. Objectif : permettre au coach de se souvenir "
        "des échanges passés lors de sessions futures. Concentre-toi sur les "
        "éléments durables (état de forme, séances, ressenti, décisions prises, "
        "objectifs évoqués, contraintes), pas sur les banalités.\n\n"
        "Renvoie UNIQUEMENT un JSON valide de la forme :\n"
        '{"summary": "3 à 5 phrases factuelles en français", '
        '"topics": ["thème1", "thème2"]}\n\n'
        f"Conversation :\n{transcript}"
    )
    parsed = chat_structured_sync([{"role": "user", "content": prompt}], timeout_s=45.0)
    if not parsed:
        return None
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        return None
    topics = parsed.get("topics")
    if not isinstance(topics, list):
        topics = []
    topics = [str(t).strip() for t in topics if str(t).strip()][:8]

    now = _now()
    conn = _connect(db_path, ctx)
    try:
        conn.execute(
            "INSERT INTO session_summaries "
            "(session_id, summary, topics, message_count, last_summarized_message_id, "
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "  summary = excluded.summary, topics = excluded.topics, "
            "  message_count = excluded.message_count, "
            "  last_summarized_message_id = excluded.last_summarized_message_id, "
            "  updated_at = excluded.updated_at",
            (
                session_id,
                summary,
                json.dumps(topics, ensure_ascii=False),
                len(messages),
                max_id,
                (existing or {}).get("created_at") or now,
                now,
            ),
        )
        emb = embed_texts_sync([summary])
        conn.execute(
            "DELETE FROM memory_vectors WHERE source_type = 'summary' AND session_id = ?",
            (session_id,),
        )
        if emb:
            conn.execute(
                "INSERT INTO memory_vectors "
                "(source_type, ref_id, session_id, text, embedding, created_at) "
                "VALUES ('summary', NULL, ?, ?, ?, ?)",
                (session_id, summary, _pack(emb[0]), now),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "session_id": session_id,
        "summary": summary,
        "topics": topics,
        "message_count": len(messages),
        "updated": True,
        "final": final,
    }


def extract_facts_from_session(
    session_id: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Extrait les faits durables d'une session et les mémorise.

    Appelée uniquement à la finalisation (inactivité ou explicite) pour borner
    le coût LLM. Retourne la liste des faits créés/mis à jour.
    """
    messages = _load_messages_with_ids(session_id, db_path=db_path, ctx=ctx)
    transcript = _transcript(messages)
    if not transcript:
        return []

    prompt = (
        "Tu extrais les FAITS DURABLES sur un cycliste à partir d'une "
        "conversation avec son coach, pour une mémoire long terme.\n"
        "Catégories autorisées :\n"
        "- preference : goûts, habitudes, matériel, créneaux d'entraînement\n"
        "- constraint : blessures, contraintes pro/familiales, indisponibilités\n"
        "- goal : objectifs sportifs (courses, FTP, poids...)\n"
        "- agreement : décisions/plans convenus avec le coach\n"
        "- personal : tout autre fait personnel utile\n\n"
        "Ne retiens QUE ce qui est explicitement dit et utile à long terme. "
        "N'invente rien. Ignore le bavardage et l'état passager (fatigue du jour).\n\n"
        "Renvoie UNIQUEMENT un JSON valide :\n"
        '{"facts": [{"category": "preference", "content": "..."}]}\n'
        'Si aucun fait durable : {"facts": []}\n\n'
        f"Conversation :\n{transcript}"
    )
    parsed = chat_structured_sync([{"role": "user", "content": prompt}], timeout_s=45.0)
    if not parsed:
        return []
    raw_facts = parsed.get("facts")
    if not isinstance(raw_facts, list):
        return []

    stored: list[dict[str, Any]] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        category = str(item.get("category") or "personal").strip()
        if not content:
            continue
        result = remember_fact(
            category,
            content,
            ctx=ctx,
            source_session_id=session_id,
            db_path=db_path,
        )
        if "error" not in result:
            stored.append(result)
    return stored


# --------------------------------------------------------------------------- #
# Vecteurs de messages + retrieval
# --------------------------------------------------------------------------- #


def index_messages_batch(
    session_id: str,
    items: list[tuple[int, str, str]],
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> None:
    """Vectorise un lot de messages ``(conversation_id, role, text)``.

    Idempotent (ignore les ``conversation_id`` déjà indexés), un seul appel
    d'embeddings par lot. Best-effort : ne lève jamais.
    """
    clean = [(cid, role, (text or "").strip()) for cid, role, text in items]
    clean = [it for it in clean if it[2]]
    if not clean:
        return
    try:
        conn = _connect(db_path, ctx)
        try:
            done = {
                int(row["ref_id"])
                for row in conn.execute(
                    "SELECT ref_id FROM memory_vectors "
                    "WHERE source_type = 'message' AND session_id = ?",
                    (session_id,),
                ).fetchall()
            }
        finally:
            conn.close()
        fresh = [it for it in clean if it[0] not in done]
        if not fresh:
            return
        embs = embed_texts_sync([it[2] for it in fresh])
        if not embs or len(embs) != len(fresh):
            return
        conn = _connect(db_path, ctx)
        try:
            now = _now()
            conn.executemany(
                "INSERT INTO memory_vectors "
                "(source_type, ref_id, session_id, text, embedding, created_at) "
                "VALUES ('message', ?, ?, ?, ?, ?)",
                [
                    (cid, session_id, text, _pack(vec), now)
                    for (cid, _role, text), vec in zip(fresh, embs, strict=False)
                ],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — best-effort
        return


def index_message(
    conversation_id: int,
    session_id: str,
    role: str,
    text: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> None:
    """Vectorise un message (idempotent sur ``conversation_id``). Best-effort."""
    index_messages_batch(session_id, [(conversation_id, role, text)], ctx=ctx, db_path=db_path)


def get_relevant_memory(
    query: str,
    *,
    k: int = _MAX_RAG_HITS,
    types: tuple[str, ...] = ("message",),
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Retourne les passages passés les plus proches sémantiquement de ``query``."""
    query = (query or "").strip()
    if not query:
        return []
    emb = embed_texts_sync([query])
    if not emb:
        return []

    conn = _connect(db_path, ctx)
    try:
        placeholders = ",".join("?" for _ in types)
        rows = conn.execute(
            f"SELECT id, source_type, ref_id, session_id, text, embedding, created_at "
            f"FROM memory_vectors WHERE source_type IN ({placeholders})",
            tuple(types),
        ).fetchall()
    finally:
        conn.close()

    hits = _cosine(emb[0], rows, k)
    return [
        {
            "source_type": row["source_type"],
            "session_id": row["session_id"],
            "text": row["text"],
            "score": round(score, 4),
        }
        for score, row in hits
    ]


def build_memory_block(
    query: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> str:
    """Assemble le bloc mémoire injecté dans le prompt système du coach.

    Contient : faits durables (toujours), résumés des sessions récentes, et
    passages passés pertinents pour ``query`` (recherche sémantique). Chaque
    partie est isolée en try/except : une panne de mémoire ne casse jamais le
    chat, elle dégrade le bloc.
    """
    sections: list[str] = []

    try:
        facts = list_facts(active_only=True, ctx=ctx, db_path=db_path)[:_MAX_FACTS]
    except Exception:  # noqa: BLE001
        facts = []
    if facts:
        by_cat: dict[str, list[str]] = {}
        for fact in facts:
            by_cat.setdefault(fact["category"], []).append(
                (fact["content"] or "")[:_MAX_FACT_CHARS]
            )
        lines = ["Ce que tu sais de l'athlète (mémoire long terme) :"]
        for category in MEMORY_CATEGORIES:
            items = by_cat.get(category)
            if not items:
                continue
            lines.append(f"- {_CATEGORY_LABELS[category]} : " + " | ".join(items))
        sections.append("\n".join(lines))

    try:
        conn = _connect(db_path, ctx)
        try:
            rows = conn.execute(
                "SELECT session_id, summary, topics, updated_at FROM session_summaries "
                "ORDER BY updated_at DESC LIMIT ?",
                (_MAX_SUMMARIES,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        rows = []
    if rows:
        lines = ["Résumé des échanges récents avec l'athlète :"]
        for row in rows:
            lines.append(f"- {row['summary'][:_MAX_SUMMARY_CHARS]}")
        sections.append("\n".join(lines))

    try:
        relevant = get_relevant_memory(
            query, k=_MAX_RAG_HITS, types=("message",), ctx=ctx, db_path=db_path
        )
    except Exception:  # noqa: BLE001
        relevant = []
    # On écarte les passages trop peu pertinents (bruit).
    relevant = [hit for hit in relevant if hit["score"] >= 0.35]
    if relevant:
        lines = ["Échanges passés potentiellement pertinents pour la question :"]
        for hit in relevant:
            lines.append(f"- {hit['text'][:_MAX_RAG_CHARS]}")
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return (
        "MÉMOIRE PERSISTANTE (issue de tes échanges passés avec l'athlète).\n"
        "Utilise-la pour la continuité, mais ne prétends jamais te souvenir "
        "d'autre chose que de ce qui figure ici ou dans les tools.\n\n" + "\n\n".join(sections)
    )


# --------------------------------------------------------------------------- #
# Finalisation / nettoyage
# --------------------------------------------------------------------------- #


def purge_session(
    session_id: str,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> None:
    """Supprime résumés + vecteurs d'une session. Les faits durables survivent.

    Leur ``source_session_id`` est nullifié pour ne pas pointer vers une session
    effacée.
    """
    conn = _connect(db_path, ctx)
    try:
        conn.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM memory_vectors WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE coach_memory SET source_session_id = NULL WHERE source_session_id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()


def finalize_idle_sessions(
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> int:
    """Finalise les sessions inactives (résumé final + extraction de faits).

    Une session est candidate si sa dernière activité dépasse
    ``SESSION_IDLE_FINALIZE_MINUTES`` et qu'elle a de nouveaux messages depuis
    son dernier résumé. Retourne le nombre de sessions finalisées. Best-effort :
    ne lève jamais (destinée à un job APScheduler).
    """
    idle_minutes = get_session_idle_finalize_minutes()
    if idle_minutes <= 0:
        return 0
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=idle_minutes)).isoformat()

    try:
        conn = _connect(db_path, ctx)
        try:
            rows = conn.execute(
                "SELECT session_id, MAX(created_at) AS last, "
                "COUNT(*) AS messages FROM conversations GROUP BY session_id"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0

    finalized = 0
    for row in rows:
        session_id = row["session_id"]
        last = (row["last"] or "").replace("Z", "+00:00")
        if last >= cutoff:
            continue
        if (row["messages"] or 0) < 2:
            continue
        try:
            summary = summarize_session(session_id, final=True, ctx=ctx, db_path=db_path)
            if summary and summary.get("updated"):
                extract_facts_from_session(session_id, ctx=ctx, db_path=db_path)
                finalized += 1
        except Exception:  # noqa: BLE001 — une session en erreur ne bloque pas les autres
            continue
    return finalized


_MEMORY_BACKFILL_FLAG = "memory_backfill_done"


def backfill_memory(
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
    max_sessions: int = 50,
) -> dict[str, Any]:
    """Vectorise une fois l'historique de conversations déjà en base.

    Le résumé/extraction des sessions reste à la charge de
    ``finalize_idle_sessions`` (les anciennes sessions sont inactives, donc
    éligibles). Idempotent via le flag ``memory_backfill_done`` (``sync_meta``) :
    ne re-travaille pas au redémarrage. Best-effort, ne lève jamais.
    """
    try:
        conn = _connect(db_path, ctx)
        try:
            flag = conn.execute(
                "SELECT value FROM sync_meta WHERE key = ?", (_MEMORY_BACKFILL_FLAG,)
            ).fetchone()
            if flag:
                return {"done": False, "reason": "already_done"}
            rows = conn.execute(
                "SELECT session_id FROM conversations GROUP BY session_id "
                "ORDER BY MIN(id) DESC LIMIT ?",
                (max_sessions,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {"done": False, "reason": "db_error"}

    sessions = [row["session_id"] for row in reversed(rows)]
    messages = 0
    for session_id in sessions:
        try:
            loaded = _load_messages_with_ids(session_id, db_path=db_path, ctx=ctx)
            items = [
                (msg["id"], msg["role"], (msg["payload"] or {}).get("content") or "")
                for msg in loaded
                if msg["role"] in ("user", "assistant")
            ]
            index_messages_batch(session_id, items, ctx=ctx, db_path=db_path)
            messages += len(items)
        except Exception:  # noqa: BLE001 — une session en erreur ne bloque pas les autres
            continue

    try:
        conn = _connect(db_path, ctx)
        try:
            conn.execute(
                "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_MEMORY_BACKFILL_FLAG, _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return {"done": True, "sessions": len(sessions), "messages": messages}


def should_summarize(session_id: str, *, ctx: AthleteContext | None = None) -> bool:
    """Indique si la session a assez de nouveaux messages pour un résumé roulant."""
    every = get_session_summary_every_messages()
    if every <= 0:
        return False
    try:
        messages = _load_messages_with_ids(session_id, ctx=ctx)
    except Exception:  # noqa: BLE001
        return False
    if not messages:
        return False
    existing = get_session_summary(session_id, ctx=ctx)
    last_id = (existing or {}).get("last_summarized_message_id") or 0
    new_count = sum(1 for msg in messages if msg["id"] > int(last_id))
    return new_count >= every


__all__ = [
    "MEMORY_CATEGORIES",
    "backfill_memory",
    "build_memory_block",
    "delete_fact",
    "extract_facts_from_session",
    "finalize_idle_sessions",
    "get_relevant_memory",
    "get_session_summary",
    "index_message",
    "index_messages_batch",
    "list_facts",
    "purge_session",
    "remember_fact",
    "should_summarize",
    "summarize_session",
    "update_fact",
]
