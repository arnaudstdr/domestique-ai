"""
Cache journalier des suggestions de séance du jour.

Évite de relancer un appel LLM à chaque ouverture du dashboard tant que les
conditions n'ont pas significativement changé. La clé combine :
- la date du jour (expire naturellement au passage de minuit),
- un hash de l'objectif (invalide si l'utilisateur édite `data/objective.yaml`),
- le TSB arrondi à l'entier (invalide si la fraîcheur change > 0.5 point).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    from domestique_ai.ingestion.strava import init_db

    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    return sqlite3.connect(path)


def objective_hash(objective: dict[str, Any] | None) -> str:
    """Hash stable d'un objectif (dict) pour détecter une modification."""
    if not objective:
        return "none"
    canonical = json.dumps(objective, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def round_tsb(tsb: float) -> float:
    """Arrondi à 1 point près pour la clé de cache."""
    return float(round(tsb))


def load(
    date: str,
    obj_hash: str,
    tsb_rounded: float,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Renvoie le payload caché ou ``None`` si absent."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM today_suggestions "
            "WHERE date = ? AND objective_hash = ? AND tsb_rounded = ?",
            (date, obj_hash, tsb_rounded),
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
    obj_hash: str,
    tsb_rounded: float,
    payload: dict[str, Any],
    source: str,
    db_path: Path | None = None,
) -> None:
    """Persiste (UPSERT) la suggestion calculée pour cette clé."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO today_suggestions "
            "(date, objective_hash, tsb_rounded, payload, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, objective_hash, tsb_rounded) DO UPDATE SET "
            "  payload = excluded.payload, "
            "  source = excluded.source, "
            "  created_at = excluded.created_at",
            (
                date,
                obj_hash,
                tsb_rounded,
                json.dumps(payload, ensure_ascii=False),
                source,
                _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate(date: str, db_path: Path | None = None) -> int:
    """Supprime toutes les entrées pour une date donnée. Retourne le nb supprimé."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM today_suggestions WHERE date = ?",
            (date,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
