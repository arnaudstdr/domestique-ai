"""Cache des semaines de plan générées par le LLM.

Une semaine de plan ne dépend que de son entrée : état de l'athlète, objectif,
dates disponibles, contraintes de génération et modèle. Tant que cette entrée
est identique (hash SHA1), la sortie est réutilisée telle quelle — double clic
sur « Générer le plan IA », relance d'une revue à état inchangé, retry d'UI.

Le cache vit dans le SQLite de l'athlète (isolation naturelle par `db_path`).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def week_hash(inputs: dict[str, Any]) -> str:
    """Hash stable (16 hex) de l'entrée complète d'une génération de semaine."""
    canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def load(hash_: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Payload de semaine caché pour ce hash, ou ``None``."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT payload FROM plan_llm_cache WHERE hash = ?", (hash_,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save(hash_: str, payload: dict[str, Any], db_path: Path | None = None) -> None:
    """Persiste (UPSERT) la sortie brute validée d'une semaine."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO plan_llm_cache (hash, payload, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(hash) DO UPDATE SET "
            "  payload = excluded.payload, "
            "  created_at = excluded.created_at",
            (
                hash_,
                json.dumps(payload, ensure_ascii=False),
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def clear(db_path: Path | None = None) -> int:
    """Purge tout le cache. Retourne le nombre d'entrées supprimées."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM plan_llm_cache")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
