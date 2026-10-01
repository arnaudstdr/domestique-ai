"""Cache persistant du brief quotidien (palier 1 du coach proactif).

Remplace l'ancien cache mémoire process : le brief survit désormais aux
redémarrages (un seul appel LLM par jour et par état, même après un deploy).
La table vit dans le SQLite de l'athlète (isolation naturelle par `ctx.db_path`).

Clé = ``date ISO + bucket TSB (pas de 5) + hash des alertes`` — identique à
l'ancienne clé mémoire, moins le `db_path` (implicite dans la DB).
"""

from __future__ import annotations

import datetime as _dt
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


def load(
    date: str,
    tsb_bucket: int,
    alerts_hash: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Renvoie le payload caché pour cette clé, ou ``None`` si absent."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM daily_brief_cache "
            "WHERE date = ? AND tsb_rounded = ? AND alerts_hash = ?",
            (date, float(tsb_bucket), alerts_hash),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save(
    date: str,
    tsb_bucket: int,
    alerts_hash: str,
    payload: dict[str, Any],
    source: str,
    db_path: Path | None = None,
) -> None:
    """Persiste (UPSERT) le brief pour cette clé et purge les jours antérieurs."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO daily_brief_cache "
            "(date, tsb_rounded, alerts_hash, payload, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, tsb_rounded, alerts_hash) DO UPDATE SET "
            "  payload = excluded.payload, "
            "  source = excluded.source, "
            "  created_at = excluded.created_at",
            (
                date,
                float(tsb_bucket),
                alerts_hash,
                json.dumps(payload, ensure_ascii=False),
                source,
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            ),
        )
        # On n'a jamais besoin des jours antérieurs (les dates ISO se comparent
        # lexicographiquement) — évite de faire grossir la table.
        conn.execute("DELETE FROM daily_brief_cache WHERE date < ?", (date,))
        conn.commit()
    finally:
        conn.close()


def clear(db_path: Path | None = None) -> int:
    """Purge tout le cache. Retourne le nombre d'entrées supprimées."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM daily_brief_cache")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
