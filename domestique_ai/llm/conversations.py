"""
Persistance des conversations coach LLM.

Chaque message Ollama est stocké en JSON brut (le format échangé avec le
modèle), avec son rôle et un session_id qui regroupe les échanges d'une
même conversation. Permet de rejouer une session ou de l'analyser plus tard.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def new_session_id() -> str:
    """Génère un identifiant unique pour une nouvelle conversation."""
    return uuid.uuid4().hex


def append_message(
    session_id: str, role: str, payload: dict[str, Any], db_path: Path | None = None
) -> int:
    """
    Ajoute un message à la conversation. Retourne l'id de la ligne insérée.

    payload : dict conforme au format Ollama
    ({"role": ..., "content": ..., "tool_calls": [...], "tool_call_id": ...}).
    """
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO conversations (session_id, created_at, role, payload) VALUES (?, ?, ?, ?)",
            (
                session_id,
                dt.datetime.now(dt.UTC).isoformat(),
                role,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def load_session(session_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Charge tous les messages d'une session, dans l'ordre d'insertion."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT payload FROM conversations WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def delete_session(session_id: str, db_path: Path | None = None) -> int:
    """Supprime tous les messages d'une session. Retourne le nombre de lignes supprimées."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        conn.execute(
            "DELETE FROM session_titles WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_session_title(session_id: str, db_path: Path | None = None) -> str | None:
    """Retourne le titre persisté d'une session, ou ``None`` si absent."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT title FROM session_titles WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def set_session_title(session_id: str, title: str, db_path: Path | None = None) -> None:
    """Persiste (UPSERT) le titre d'une session."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO session_titles (session_id, title, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "  title = excluded.title, updated_at = excluded.updated_at",
            (
                session_id,
                title.strip(),
                dt.datetime.now(dt.UTC).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def generate_session_title(session_id: str, db_path: Path | None = None) -> str | None:
    """Génère un titre court (3-6 mots) via Ollama et le persiste.

    Best-effort : si Ollama échoue ou si la session est vide, retourne ``None``
    sans rien persister. Appelée en arrière-plan par le router après le 1er
    échange complet — l'utilisateur a déjà sa réponse, le titre arrive ~5 s
    plus tard via le poll régulier des sessions.
    """
    from domestique_ai.llm.ollama_client import OllamaError, stream_chat

    messages = load_session(session_id, db_path=db_path)
    excerpts: list[str] = []
    for msg in messages[:6]:  # max 3 paires user / assistant
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            excerpts.append(f"[USER]: {content[:300]}")
        elif role == "assistant":
            excerpts.append(f"[COACH]: {content[:300]}")
    if not excerpts:
        return None

    prompt = (
        "Donne un titre court (3 à 6 mots, en français) qui résume cette "
        "conversation entre un cycliste et son coach d'endurance. Renvoie "
        "UNIQUEMENT le titre, sans guillemets ni ponctuation finale, sans "
        'préfixe "Titre :".\n\n' + "\n".join(excerpts)
    )

    title = ""
    try:
        async for chunk in stream_chat(
            [{"role": "user", "content": prompt}],
            think=False,
        ):
            title += chunk.get("content") or ""
    except OllamaError:
        return None

    title = title.strip().strip("\"'").rstrip(".").strip()
    if not title:
        return None
    title = title[:80]
    set_session_title(session_id, title, db_path=db_path)
    return title


def list_sessions(limit: int = 50, db_path: Path | None = None) -> list[dict[str, Any]]:
    """
    Liste les sessions (les plus récentes en premier) avec leur titre généré
    (s'il existe) et leur premier message utilisateur comme aperçu de secours.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT session_id, MIN(created_at) AS started_at, "
            "COUNT(*) AS messages "
            "FROM conversations GROUP BY session_id "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        sessions = []
        for session_id, started_at, count in rows:
            preview_row = conn.execute(
                "SELECT payload FROM conversations "
                "WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            preview = ""
            if preview_row:
                payload = json.loads(preview_row[0])
                preview = (payload.get("content") or "")[:80]
            title_row = conn.execute(
                "SELECT title FROM session_titles WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sessions.append(
                {
                    "session_id": session_id,
                    "started_at": started_at,
                    "messages": count,
                    "preview": preview,
                    "title": title_row[0] if title_row else None,
                }
            )
        return sessions
    finally:
        conn.close()
