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


def _row_to_message(row: sqlite3.Row) -> dict[str, Any] | None:
    """Convertit une ligne ``conversations`` en message affichable, ou ``None``.

    Ne garde que les tours ``user`` / ``assistant`` et écarte les messages user
    vides (legacy) — même filtrage que l'endpoint historique par session.
    """
    role = row["role"]
    if role not in ("user", "assistant"):
        return None
    payload = json.loads(row["payload"])
    content = payload.get("content") or ""
    if role == "user" and not content.strip():
        return None
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": role,
        "content": content,
        "thinking": payload.get("thinking"),
        "tool_calls": payload.get("tool_calls"),
    }


def _fetch_thread_rows(
    conn: sqlite3.Connection,
    condition: str,
    params: tuple[Any, ...],
    *,
    order: str,
    limit: int,
) -> list[sqlite3.Row]:
    """Récupère ``limit + 1`` lignes du fil (la ligne surnuméraire sert de flag)."""
    where = "role IN ('user', 'assistant')"
    if condition:
        where += f" AND {condition}"
    sql = (
        "SELECT id, session_id, role, payload FROM conversations "
        f"WHERE {where} ORDER BY id {order} LIMIT ?"
    )
    return conn.execute(sql, (*params, limit + 1)).fetchall()


def load_thread_page(
    *,
    limit: int = 30,
    before: int | None = None,
    after: int | None = None,
    anchor: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Charge une page du fil unique, toutes sessions confondues.

    Le fil est l'ensemble des messages ``user`` / ``assistant`` de l'athlète,
    ordonnés par ``id`` croissant (toutes sessions fusionnées). Trois modes :

    - sans curseur : les ``limit`` derniers messages ;
    - ``before`` : la page juste au-dessus du curseur (remontée) ;
    - ``after`` : la page juste en dessous du curseur (redescente) ;
    - ``anchor`` : fenêtre centrée sur un id (saut depuis la recherche).

    Retourne ``{"messages": [...asc], "has_more_before": bool,
    "has_more_after": bool}``. Chaque message porte ``id``, ``session_id``,
    ``role``, ``content``, ``thinking`` et ``tool_calls``.
    """
    limit = max(1, min(limit, 200))
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if anchor is not None:
            half = max(1, limit // 2)
            older_rows = _fetch_thread_rows(conn, "id <= ?", (anchor,), order="DESC", limit=half)
            older = [m for m in map(_row_to_message, older_rows[:half]) if m is not None]
            newer_limit = max(1, limit - len(older))
            newer_rows = _fetch_thread_rows(
                conn, "id > ?", (anchor,), order="ASC", limit=newer_limit
            )
            newer = [m for m in map(_row_to_message, newer_rows[:newer_limit]) if m is not None]
            messages = list(reversed(older)) + newer
            has_more_before = len(older_rows) > half
            has_more_after = len(newer_rows) > newer_limit
        elif before is not None:
            rows = _fetch_thread_rows(conn, "id < ?", (before,), order="DESC", limit=limit)
            has_more_before = len(rows) > limit
            messages = list(reversed([m for m in map(_row_to_message, rows[:limit]) if m]))
            has_more_after = True
        elif after is not None:
            rows = _fetch_thread_rows(conn, "id > ?", (after,), order="ASC", limit=limit)
            has_more_after = len(rows) > limit
            messages = [m for m in map(_row_to_message, rows[:limit]) if m]
            has_more_before = True
        else:
            rows = _fetch_thread_rows(conn, "", (), order="DESC", limit=limit)
            has_more_before = len(rows) > limit
            messages = list(reversed([m for m in map(_row_to_message, rows[:limit]) if m]))
            has_more_after = False
    finally:
        conn.close()
    return {
        "messages": messages,
        "has_more_before": has_more_before,
        "has_more_after": has_more_after,
    }


def current_or_new_session(idle_minutes: int, db_path: Path | None = None) -> dict[str, Any]:
    """Résout la session interne courante du fil unique.

    Réutilise la session la plus récente si son dernier message date de moins de
    ``idle_minutes`` minutes ; sinon en ouvre une nouvelle. ``idle_minutes <= 0``
    désactive la rotation (le fil reste une session unique).

    Retourne ``{"session_id", "rotated", "previous_session_id"}`` — l'appelant
    peut finaliser ``previous_session_id`` en tâche de fond après une rotation.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT session_id, created_at FROM conversations ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"session_id": new_session_id(), "rotated": True, "previous_session_id": None}

    session_id, created_at = row[0], row[1]
    if idle_minutes <= 0:
        return {"session_id": session_id, "rotated": False, "previous_session_id": None}

    try:
        last = dt.datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return {"session_id": session_id, "rotated": False, "previous_session_id": None}
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.UTC)
    if last < dt.datetime.now(dt.UTC) - dt.timedelta(minutes=idle_minutes):
        return {
            "session_id": new_session_id(),
            "rotated": True,
            "previous_session_id": session_id,
        }
    return {"session_id": session_id, "rotated": False, "previous_session_id": None}


def delete_session(session_id: str, db_path: Path | None = None) -> int:
    """Supprime tous les messages d'une session. Retourne le nombre de lignes supprimées."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def list_sessions(limit: int = 50, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Liste les sessions (les plus récentes en premier) + aperçu du 1er message.

    Endpoint historique : l'UI affiche un fil unique sans sélecteur de session,
    et la génération de titre a été retirée (plus aucun appel Ollama pour ça).
    ``title`` est conservé à ``None`` pour la compat de schéma de réponse.
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
            sessions.append(
                {
                    "session_id": session_id,
                    "started_at": started_at,
                    "messages": count,
                    "preview": preview,
                    "title": None,
                }
            )
        return sessions
    finally:
        conn.close()
