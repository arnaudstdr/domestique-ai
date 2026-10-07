"""Backfill one-off des streams Garmin alignés (base des montées/climbs).

Les streams Garmin ne sont pas persistés au sync (l'API streams les re-fetch en
live). Ce module les télécharge **une fois** pour les activités Garmin qui n'en
ont pas, les compacte (~1 point / 5 s) et les stocke dans ``activity_streams``
via le même format que TCX — ``processing/climbs.py`` peut alors détecter les
montées sans appel réseau.

Usage ponctuel (ne tourne pas dans le scheduler) :

    python -m domestique_ai.ingestion.backfill_streams --all
    python -m domestique_ai.ingestion.backfill_streams --public-id <id> --limit 10

Le flag ``garmin_streams_backfill_done`` (``sync_meta``) est posé quand un
passage complet s'est terminé sans erreur ; le backfill reste **reprenable** :
la sélection se fait par « activités sans streams », pas par le flag.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import get_sync_meta, init_db, set_sync_meta, store_activity_streams
from domestique_ai.ingestion.garmin import (
    _ingest_client_for,  # noqa: PLC2701 — même paquet
    compact_aligned_series,
    parse_details_aligned,
)

log = logging.getLogger(__name__)

STREAMS_BACKFILL_FLAG = "garmin_streams_backfill_done"
DEFAULT_DELAY_SEC = 0.8
#: Séries minimales pour qu'un tracé soit exploitable par la détection de montées.
_REQUIRED_SERIES = ("time", "distance", "altitude")


def _resolve_path(db_path: Path | str | None, ctx: AthleteContext | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if ctx is not None:
        return Path(ctx.db_path)
    return Path(get_db_path())


def missing_stream_activity_ids(
    db_path: Path | str | None = None,
    *,
    ctx: AthleteContext | None = None,
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """``(activity_id local, garmin_id)`` des activités Garmin sans streams persistés."""
    path = _resolve_path(db_path, ctx)
    if not path.exists():
        return []
    init_db(path)
    sql = (
        "SELECT a.id, a.garmin_id FROM activities a "
        "LEFT JOIN activity_streams s ON s.activity_id = a.id "
        "WHERE a.garmin_id IS NOT NULL AND s.activity_id IS NULL "
        "ORDER BY a.date ASC"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [(int(row[0]), str(row[1])) for row in rows]


def backfill_streams_for_athlete(
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | str | None = None,
    client: Any | None = None,
    limit: int | None = None,
    delay_sec: float = DEFAULT_DELAY_SEC,
    dry_run: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, int | bool]:
    """Télécharge et persiste les streams manquants d'un athlète.

    Best-effort : une activité en échec n'interrompt pas le lot. Retourne un
    résumé ``{missing, fetched, stored, skipped, errors, forced_refetch}`` ;
    ``errors == 0`` autorise la pose du flag de backfill.
    """
    path = _resolve_path(db_path, ctx)
    targets = missing_stream_activity_ids(path, ctx=ctx, limit=limit)
    summary: dict[str, int | bool] = {
        "missing": len(targets),
        "fetched": 0,
        "stored": 0,
        "skipped": 0,
        "errors": 0,
        "forced_refetch": False,
    }
    if dry_run or not targets:
        return summary

    owns_client = client is None
    if owns_client:
        client = _ingest_client_for(ctx)

    for index, (activity_id, garmin_id) in enumerate(targets):
        if index > 0 and delay_sec > 0:
            sleep_fn(delay_sec)
        try:
            details = client.get_activity_details(str(garmin_id))
        except Exception:  # noqa: BLE001 — une activité KO n'arrête pas le lot
            log.warning("Backfill streams %s : détails indisponibles.", garmin_id, exc_info=True)
            summary["errors"] = int(summary["errors"]) + 1
            continue
        summary["fetched"] = int(summary["fetched"]) + 1
        aligned = parse_details_aligned(details)
        if not all(aligned.get(key) for key in _REQUIRED_SERIES):
            summary["skipped"] = int(summary["skipped"]) + 1
            continue
        payload = compact_aligned_series(aligned)
        store_activity_streams(activity_id, payload, db_path=path)
        summary["stored"] = int(summary["stored"]) + 1

    if int(summary["errors"]) == 0:
        set_sync_meta(STREAMS_BACKFILL_FLAG, time.strftime("%Y-%m-%dT%H:%M:%S"), path)
    return summary


def _iter_contexts(
    args: argparse.Namespace, *, require_tokens: bool = True
) -> list[AthleteContext]:
    from domestique_ai.athlete_context import context_for_athlete
    from domestique_ai.config import garmin_token_dir_for
    from domestique_ai.export.garmin_connect import token_cache_present
    from domestique_ai.platform_db import (
        get_or_create_bootstrap_coach,
        get_user_by_public_id,
        list_users,
    )

    if args.public_id:
        user = get_user_by_public_id(args.public_id)
        if user is None:
            print(f"Athlète introuvable : {args.public_id}", file=sys.stderr)
            return []
        users = [user]
    else:
        users = list_users()
        if not any(u.get("is_bootstrap") for u in users):
            get_or_create_bootstrap_coach()
            users = list_users()

    contexts = []
    for user in users:
        try:
            ctx = context_for_athlete(user)
        except Exception:  # noqa: BLE001
            log.warning("Contexte athlète indisponible : %s", user.get("public_id"), exc_info=True)
            continue
        if require_tokens and not token_cache_present(garmin_token_dir_for(ctx)):
            if args.public_id:
                print(
                    f"Aucun token Garmin pour {args.public_id} — rien à faire.",
                    file=sys.stderr,
                )
            continue
        contexts.append(ctx)
    return contexts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill one-off des streams Garmin (activités sans streams).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Tous les athlètes (défaut ; flag accepté pour la compatibilité de la doc).",
    )
    parser.add_argument("--public-id", help="Limiter à un athlète (défaut : tous).")
    parser.add_argument("--limit", type=int, help="Nombre max d'activités par athlète.")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SEC,
        help=f"Délai entre appels Garmin en secondes (défaut {DEFAULT_DELAY_SEC}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Lister sans télécharger.")
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Ne pas reconstruire les montées après le backfill.",
    )
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Reconstruire les montées (rebuild + backfill des tracés) sans télécharger de "
        "streams — utile après une migration de schéma.",
    )
    parser.add_argument("--force", action="store_true", help="Relancer même si le flag est posé.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    contexts = _iter_contexts(args, require_tokens=not args.rebuild_only)
    if not contexts:
        print("Aucun athlète." if args.rebuild_only else "Aucun athlète avec tokens Garmin.")
        return 1

    exit_code = 0
    for ctx in contexts:
        path = Path(ctx.db_path)
        if args.rebuild_only:
            from domestique_ai.processing.climbs import rebuild_climbs

            report = rebuild_climbs(ctx=ctx)
            print(f"[{ctx.public_id}] montées : {json.dumps(report)}")
            continue
        if not args.force and get_sync_meta(STREAMS_BACKFILL_FLAG, path) is not None:
            print(
                f"[{ctx.public_id}] flag {STREAMS_BACKFILL_FLAG} déjà posé — skip (--force pour relancer)."
            )
            continue
        summary = backfill_streams_for_athlete(
            ctx=ctx,
            limit=args.limit,
            delay_sec=args.delay,
            dry_run=args.dry_run,
        )
        print(f"[{ctx.public_id}] {json.dumps(summary)}")
        if args.dry_run:
            continue
        if not args.no_rebuild and int(summary["stored"]) > 0:
            from domestique_ai.processing.climbs import rebuild_climbs

            report = rebuild_climbs(ctx=ctx)
            print(f"[{ctx.public_id}] montées : {json.dumps(report)}")
        if int(summary["errors"]) > 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
