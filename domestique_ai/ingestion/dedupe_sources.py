"""Déduplication des activités double-source (Strava legacy + Garmin).

Le passage Strava → Garmin (09/2026) a laissé des espaces athlètes avec les
deux sources : la même sortie physique existe une fois côté Strava
(``strava_id``) et une fois côté Garmin (``garmin_id``). Tout le pipeline
(TSS, CTL/ATL/TSB, volume, tendances) comptait alors chaque activité deux
fois.

Ce module apparie chaque activité Garmin à son jumeau Strava — même jour UTC,
même bucket sport, distance à ±2 % (la durée n'est pas fiable : l'app Strava
peut rester « en pause » et gonfler le temps) —, recopie sur la ligne Garmin
les champs NULL du jumeau (ex. ``avg_power`` absent du payload liste Garmin),
puis supprime les lignes Strava doublons. Les doublons Strava internes (double
upload) sont également nettoyés. Les activités d'une seule source (orphelines)
sont toujours conservées.

Usage :

    python -m domestique_ai.ingestion.dedupe_sources                 # dry-run
    python -m domestique_ai.ingestion.dedupe_sources --apply         # exécute
    python -m domestique_ai.ingestion.dedupe_sources --db PATH       # cible
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
from pathlib import Path

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db

_DISTANCE_TOLERANCE = 0.02

# Champs enrichis : recopiés du jumeau Strava quand ils manquent côté Garmin.
_MERGE_COLUMNS = (
    "avg_heart_rate",
    "max_heart_rate",
    "avg_power",
    "elevation_gain",
    "hr_z1_time",
    "hr_z2_time",
    "hr_z3_time",
    "hr_z4_time",
    "hr_z5_time",
)

_ROW_COLUMNS = (
    "id",
    "strava_id",
    "garmin_id",
    "date",
    "duration",
    "sport_type",
    "distance",
)


def _bucket(sport_type: str | None) -> str:
    """Bucket sport pour l'appariement.

    Strava tagge les séances Zwift ``Ride`` là où Garmin met ``VirtualRide`` —
    le bucket ``ride`` englobe les deux pour rester insensible à la source.
    """
    if sport_type and "Ride" in sport_type:
        return "ride"
    if sport_type in ("Run", "TrailRun", "TreadmillRun"):
        return "run"
    if sport_type in ("Walk", "Hike"):
        return "walk"
    return sport_type or "?"


def _day(iso: str | None) -> dt.date | None:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _duration_diff(a: float | None, b: float | None) -> float:
    if a is not None and b is not None:
        return abs(a - b)
    return 0.0


def _load(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Charge les lignes avec distance exploitable, triées par date."""
    rows = conn.execute(
        "SELECT id, strava_id, garmin_id, date, duration, sport_type, distance "
        "FROM activities WHERE distance IS NOT NULL AND distance > 0 "
        "ORDER BY date ASC"
    ).fetchall()
    strava: list[dict] = []
    garmin: list[dict] = []
    for r in rows:
        row = dict(zip(_ROW_COLUMNS, r, strict=True))
        if row["strava_id"] is not None:
            strava.append(row)
        if row["garmin_id"] is not None:
            garmin.append(row)
    return strava, garmin


def find_duplicate_pairs(conn: sqlite3.Connection) -> list[tuple[dict, dict]]:
    """Apparie chaque activité Garmin à son jumeau Strava (greedy, 1-pour-1).

    Retourne une liste de ``(garmin, strava)`` : la ligne Strava est un doublon
    de la ligne Garmin et peut être supprimée.
    """
    strava, garmin = _load(conn)
    matched: set[int] = set()
    pairs: list[tuple[dict, dict]] = []
    for g in garmin:
        gday = _day(g["date"])
        gbucket = _bucket(g["sport_type"])
        if gday is None:
            continue
        candidates = [
            s
            for s in strava
            if s["id"] not in matched
            and _day(s["date"]) == gday
            and _bucket(s["sport_type"]) == gbucket
            and abs(s["distance"] - g["distance"]) / g["distance"] <= _DISTANCE_TOLERANCE
        ]
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda s: (
                abs(s["distance"] - g["distance"]),
                _duration_diff(s["duration"], g["duration"]),
            ),
        )
        matched.add(best["id"])
        pairs.append((g, best))
    return pairs


def find_internal_strava_duplicates(
    conn: sqlite3.Connection, *, matched_ids: set[int] | None = None
) -> list[dict]:
    """Doublons Strava internes : même jour + bucket + distance (±0,1 %) + durée (±2 %).

    Les lignes Strava identiques sont regroupées par clé. Deux cas :

    - le groupe contient une ligne déjà appariée à un jumeau Garmin
      (``matched_ids``) : tout le groupe représente la même sortie que la ligne
      Garmin → toutes les autres occurrences sont des doublons à supprimer ;
    - sinon (pas de jumeau Garmin) : on garde la 1ʳᵉ occurrence, les suivantes
      sont des doublons (double upload).
    """
    strava, _ = _load(conn)
    groups: dict[tuple, list[dict]] = {}
    for s in strava:
        key = (
            _day(s["date"]),
            _bucket(s["sport_type"]),
            round(s["distance"] / 1000, 1),
            round(s["duration"] / 30) if s["duration"] else None,
        )
        groups.setdefault(key, []).append(s)

    matched = matched_ids or set()
    duplicates: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if any(m["id"] in matched for m in members):
            duplicates.extend(m for m in members if m["id"] not in matched)
        else:
            duplicates.extend(members[1:])
    return duplicates


def _merge_values(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple]:
    """Charge les colonnes de merge pour ``ids`` (une requête)."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    cols = ", ".join(_MERGE_COLUMNS)
    rows = conn.execute(
        f"SELECT id, {cols} FROM activities WHERE id IN ({placeholders})", ids
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def _merge_and_delete(
    conn: sqlite3.Connection,
    pairs: list[tuple[dict, dict]],
    internal: list[dict],
) -> dict[str, int]:
    """Recopie les champs NULL du jumeau Strava sur la ligne Garmin puis
    supprime les lignes Strava doublons. Retourne (merged, deleted)."""
    gids = [g["id"] for g, _ in pairs]
    sids = [s["id"] for _, s in pairs] + [s["id"] for s in internal]
    gvals = _merge_values(conn, gids)
    svalues = _merge_values(conn, sids)
    merged = 0
    for g, s in pairs:
        sets: list[str] = []
        params: list[float] = []
        for i, col in enumerate(_MERGE_COLUMNS):
            sval = svalues.get(s["id"], (None,) * len(_MERGE_COLUMNS))[i]
            gval = gvals.get(g["id"], (None,) * len(_MERGE_COLUMNS))[i]
            if sval is not None and gval is None:
                sets.append(f"{col} = ?")
                params.append(sval)
        if sets:
            conn.execute(
                f"UPDATE activities SET {', '.join(sets)} WHERE id = ?",
                (*params, g["id"]),
            )
            merged += 1
    conn.executemany("DELETE FROM activities WHERE id = ?", [(i,) for i in sids])
    return {"merged": merged, "deleted": len(sids)}


def dedupe_activities_db(
    db_path: Path | None = None,
    *,
    dry_run: bool = True,
    backup: bool = True,
) -> dict:
    """Déduplique la base ``db_path`` (défaut : config).

    Retourne un rapport dict avec les paires trouvées et, si exécution réelle,
    les compteurs merge/suppression. Un backup horodaté ``<db>.bak-<timestamp>``
    est posé avant toute modification.
    """
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        pairs = find_duplicate_pairs(conn)
        matched_ids = {s["id"] for _, s in pairs}
        internal = find_internal_strava_duplicates(conn, matched_ids=matched_ids)
        report = {
            "db": str(path),
            "pairs": pairs,
            "internal_strava": internal,
            "deleted": 0,
            "merged": 0,
            "dry_run": dry_run,
        }
        if not dry_run and (pairs or internal):
            if backup:
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
            result = _merge_and_delete(conn, pairs, internal)
            conn.commit()
            report["deleted"] = result["deleted"]
            report["merged"] = result["merged"]
        elif not dry_run:
            conn.commit()
        return report
    finally:
        conn.close()


def _print_report(report: dict) -> None:
    pairs: list[tuple[dict, dict]] = report["pairs"]
    internal: list[dict] = report["internal_strava"]
    km = sum(s["distance"] for _, s in pairs) / 1000
    print(f"DB    : {report['db']}")
    print(f"Paires Garmin↔Strava : {len(pairs)}  ({km:.1f} km Strava doublons)")
    for g, s in pairs:
        print(
            f"  {g['date'][:10]}  {s['sport_type']:12} "
            f"Garmin {g['distance'] / 1000:6.1f} km  /  Strava {s['distance'] / 1000:6.1f} km"
        )
    print(f"Doublons Strava internes : {len(internal)}")
    for s in internal:
        print(f"  {s['date'][:10]}  {s['sport_type']:12}  {s['distance'] / 1000:.1f} km")
    if report["dry_run"]:
        print("Dry-run : rien n'a été modifié. Relancez avec --apply pour exécuter.")
    else:
        print(f"Exécuté : {report['deleted']} lignes supprimées, "
              f"{report['merged']} lignes Garmin enrichies depuis leur jumeau Strava.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Base à dédupliquer (défaut : config.get_db_path())")
    parser.add_argument("--apply", action="store_true", help="Exécute la suppression (sinon dry-run)")
    args = parser.parse_args(argv)

    db = Path(args.db) if args.db else get_db_path()
    report = dedupe_activities_db(db, dry_run=not args.apply)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())