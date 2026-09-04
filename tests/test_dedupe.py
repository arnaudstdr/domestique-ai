"""Tests de la déduplication double-source (Strava legacy + Garmin)."""

from __future__ import annotations

import sqlite3

from domestique_ai.ingestion.db import init_db
from domestique_ai.ingestion.dedupe_sources import (
    dedupe_activities_db,
    find_duplicate_pairs,
    find_internal_strava_duplicates,
)


def _insert(
    conn: sqlite3.Connection,
    *,
    strava_id: int | None = None,
    garmin_id: int | None = None,
    date: str,
    distance: float,
    duration: float | None = 3600,
    sport_type: str = "Ride",
    **extra: float | None,
) -> None:
    cols = ["date", "distance", "duration", "sport_type"]
    vals: list = [date, distance, duration, sport_type]
    if strava_id is not None:
        cols.append("strava_id")
        vals.append(strava_id)
    if garmin_id is not None:
        cols.append("garmin_id")
        vals.append(garmin_id)
    for key, value in extra.items():
        cols.append(key)
        vals.append(value)
    conn.execute(
        f"INSERT INTO activities ({', '.join(cols)}) VALUES ({', '.join('?' * len(vals))})",
        vals,
    )


def _db(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "dedupe.db"
    init_db(db)
    return sqlite3.connect(db)


def test_matches_exact_twin(tmp_path):
    conn = _db(tmp_path)
    _insert(conn, strava_id=1, date="2026-05-06T10:00:00Z", distance=16300.0, sport_type="Ride")
    _insert(conn, garmin_id=101, date="2026-05-06T10:00:00Z", distance=16300.0, sport_type="Ride")
    conn.commit()
    pairs = find_duplicate_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0][0]["garmin_id"] == 101
    assert pairs[0][1]["strava_id"] == 1


def test_zwift_bucket_matching(tmp_path):
    """Strava tagge Zwift `Ride`, Garmin `VirtualRide` — même sortie quand même."""
    conn = _db(tmp_path)
    _insert(conn, strava_id=1, date="2026-05-13T10:00:00Z", distance=13700.0, sport_type="Ride")
    _insert(
        conn, garmin_id=101, date="2026-05-13T10:00:00Z", distance=13700.0, sport_type="VirtualRide"
    )
    conn.commit()
    assert len(find_duplicate_pairs(conn)) == 1


def test_duration_anomaly_ignored(tmp_path):
    """La durée Strava peut être gonflée (enregistrement laissé en pause) :
    seul le jour + bucket + distance comptent."""
    conn = _db(tmp_path)
    _insert(
        conn,
        strava_id=1,
        date="2026-05-25T10:00:00Z",
        distance=35900.0,
        duration=12300,
        sport_type="Ride",
    )
    _insert(
        conn,
        garmin_id=101,
        date="2026-05-25T10:00:00Z",
        distance=35900.0,
        duration=4800,
        sport_type="Ride",
    )
    conn.commit()
    assert len(find_duplicate_pairs(conn)) == 1


def test_distance_tolerance(tmp_path):
    conn = _db(tmp_path)
    # ±2 % : 1,5 % → apparié ; 3 % → orphelin.
    _insert(conn, strava_id=1, date="2026-05-06T10:00:00Z", distance=10000.0)
    _insert(conn, garmin_id=101, date="2026-05-06T10:00:00Z", distance=10150.0)
    _insert(conn, strava_id=2, date="2026-05-07T10:00:00Z", distance=10000.0)
    _insert(conn, garmin_id=102, date="2026-05-07T10:00:00Z", distance=10300.0)
    conn.commit()
    pairs = find_duplicate_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0][0]["garmin_id"] == 101


def test_orphans_kept(tmp_path):
    """Lignes sans jumeau de l'autre source : jamais proposées à la suppression."""
    conn = _db(tmp_path)
    _insert(
        conn, strava_id=1, date="2026-01-02T10:00:00Z", distance=14000.0, sport_type="VirtualRide"
    )
    _insert(conn, garmin_id=101, date="2026-09-02T15:33:23Z", distance=36616.0, sport_type="Ride")
    conn.commit()
    assert find_duplicate_pairs(conn) == []


def test_different_bucket_not_matched(tmp_path):
    conn = _db(tmp_path)
    _insert(conn, strava_id=1, date="2026-05-06T10:00:00Z", distance=10000.0, sport_type="Walk")
    _insert(conn, garmin_id=101, date="2026-05-06T10:00:00Z", distance=10000.0, sport_type="Ride")
    conn.commit()
    assert find_duplicate_pairs(conn) == []


def test_internal_strava_duplicate(tmp_path):
    conn = _db(tmp_path)
    _insert(
        conn,
        strava_id=1,
        date="2026-05-20T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    _insert(
        conn,
        strava_id=2,
        date="2026-05-20T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    conn.commit()
    internal = find_internal_strava_duplicates(conn)
    assert len(internal) == 1
    assert internal[0]["strava_id"] == 2


def test_internal_duplicate_matched_as_pair_not_double_counted(tmp_path):
    """Deux lignes Strava identiques + un jumeau Garmin : une part en paire,
    l'autre en doublon interne — les deux doivent être supprimées."""
    conn = _db(tmp_path)
    _insert(
        conn,
        strava_id=1,
        date="2026-05-20T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    _insert(
        conn,
        strava_id=2,
        date="2026-05-20T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    _insert(
        conn,
        garmin_id=101,
        date="2026-05-20T10:00:00Z",
        distance=20704.4,
        duration=3602,
        sport_type="VirtualRide",
    )
    conn.commit()
    pairs = find_duplicate_pairs(conn)
    matched = {s["id"] for _, s in pairs}
    internal = find_internal_strava_duplicates(conn, matched_ids=matched)
    assert len(pairs) == 1
    assert len(internal) == 1
    assert pairs[0][1]["id"] != internal[0]["id"]
    db_path = tmp_path / "dedupe.db"
    report = dedupe_activities_db(db_path, dry_run=False)
    assert report["deleted"] == 2
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM activities WHERE strava_id IS NOT NULL").fetchone()[
                0
            ]
            == 0
        )
    finally:
        conn.close()


def test_dedupe_merges_and_deletes(tmp_path):
    conn = _db(tmp_path)
    _insert(
        conn,
        strava_id=1,
        date="2026-05-06T10:00:00Z",
        distance=16300.0,
        sport_type="Ride",
        avg_power=250.0,
    )
    _insert(
        conn,
        garmin_id=101,
        date="2026-05-06T10:00:00Z",
        distance=16300.0,
        sport_type="Ride",
        avg_power=None,
    )
    _insert(
        conn,
        strava_id=2,
        date="2026-05-07T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    _insert(
        conn,
        strava_id=3,
        date="2026-05-07T10:00:00Z",
        distance=20700.0,
        duration=3840,
        sport_type="Ride",
    )
    _insert(
        conn, strava_id=4, date="2026-01-02T10:00:00Z", distance=14000.0, sport_type="VirtualRide"
    )
    conn.commit()

    db_path = tmp_path / "dedupe.db"
    report = dedupe_activities_db(db_path, dry_run=True)
    assert report["dry_run"] is True
    assert len(report["pairs"]) == 1
    assert len(report["internal_strava"]) == 1
    assert report["deleted"] == 0

    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 5

    report = dedupe_activities_db(db_path, dry_run=False)
    assert report["deleted"] == 2
    assert report["merged"] == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 3
        g = conn.execute("SELECT avg_power FROM activities WHERE garmin_id = 101").fetchone()
        assert g[0] == 250.0
        assert (
            conn.execute("SELECT COUNT(*) FROM activities WHERE strava_id = 1").fetchone()[0] == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM activities WHERE strava_id = 4").fetchone()[0] == 1
        )
    finally:
        conn.close()


def test_backup_created_on_apply(tmp_path):
    conn = _db(tmp_path)
    _insert(conn, strava_id=1, date="2026-05-06T10:00:00Z", distance=16300.0)
    _insert(conn, garmin_id=101, date="2026-05-06T10:00:00Z", distance=16300.0)
    conn.commit()
    conn.close()
    db_path = tmp_path / "dedupe.db"
    dedupe_activities_db(db_path, dry_run=False)
    assert list(tmp_path.glob("dedupe.db.bak-*"))
