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


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    # Import local pour éviter les cycles avec ingestion.strava.
    from domestique_ai.ingestion.strava import init_db
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def new_session_id() -> str:
    """Génère un identifiant unique pour une nouvelle conversation."""
    return uuid.uuid4().hex


def append_message(session_id: str, role: str, payload: dict[str, Any],
                   db_path: Path | None = None) -> int:
    """
    Ajoute un message à la conversation. Retourne l'id de la ligne insérée.

    payload : dict conforme au format Ollama
    ({"role": ..., "content": ..., "tool_calls": [...], "tool_call_id": ...}).
    """
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO conversations (session_id, created_at, role, payload) "
            "VALUES (?, ?, ?, ?)",
            (
                session_id,
                dt.datetime.now(dt.timezone.utc).isoformat(),
                role,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def load_session(session_id: str,
                 db_path: Path | None = None) -> list[dict[str, Any]]:
    """Charge tous les messages d'une session, dans l'ordre d'insertion."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT payload FROM conversations "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def list_sessions(limit: int = 50,
                  db_path: Path | None = None) -> list[dict[str, Any]]:
    """
    Liste les sessions (les plus récentes en premier) avec leur premier
    message utilisateur comme aperçu.
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
            sessions.append({
                "session_id": session_id,
                "started_at": started_at,
                "messages": count,
                "preview": preview,
            })
        return sessions
    finally:
        conn.close()
