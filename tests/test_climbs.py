"""Tests détection/appariement/statistiques des montées (processing.climbs)."""

from __future__ import annotations

import sqlite3

from domestique_ai.ingestion.db import delete_activity, init_db, store_activity_streams
from domestique_ai.llm.tools import dispatch
from domestique_ai.processing.climbs import (
    climb_detail,
    climb_report,
    detect_climbs,
    rebuild_climbs,
    rename_climb,
)

STEP_SEC = 5.0
SPEED_MPS = 6.0


def _hill_payload(
    *,
    start_lat: float = 48.0,
    start_lng: float = 7.0,
    base_alt: float = 300.0,
    flat_m: float = 1000.0,
    climb_m: float = 2000.0,
    gradient_pct: float = 5.0,
    tail_m: float = 1000.0,
) -> dict:
    total = flat_m + climb_m + tail_m
    time, distance, altitude, lat, lng = [], [], [], [], []
    dist = 0.0
    t = 0.0
    while dist <= total:
        if dist < flat_m:
            alt = base_alt
        elif dist < flat_m + climb_m:
            alt = base_alt + (dist - flat_m) * gradient_pct / 100
        else:
            alt = base_alt + climb_m * gradient_pct / 100
        time.append(t)
        distance.append(dist)
        altitude.append(alt)
        lat.append(start_lat + dist / 111_000.0)
        lng.append(start_lng)
        dist += SPEED_MPS * STEP_SEC
        t += STEP_SEC
    return {
        "time": time,
        "distance": distance,
        "altitude": altitude,
        "lat": lat,
        "lng": lng,
        "heartrate": [140.0] * len(time),
    }


def _seed_activity(db_path, activity_id: int, date: str, payload: dict, strava_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (id, strava_id, date, duration, sport_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (activity_id, strava_id, date, 3000, "Ride"),
        )
        conn.commit()
    finally:
        conn.close()
    store_activity_streams(activity_id, payload, db_path=db_path)


def test_detect_climbs_finds_hill():
    climbs = detect_climbs(_hill_payload())
    assert len(climbs) == 1
    climb = climbs[0]
    assert 1800 <= climb["length_m"] <= 2100
    assert 85 <= climb["gain_m"] <= 105
    assert 4.5 <= climb["avg_gradient_pct"] <= 5.5
    assert 700 <= climb["vam_m_h"] <= 1200
    assert climb["duration_sec"] > 250
    assert climb["avg_hr"] == 140.0
    assert climb["start_lat"] is not None


def test_detect_climbs_ignores_flat_and_short():
    flat = _hill_payload(flat_m=5000.0, climb_m=0.0, tail_m=0.0)
    assert detect_climbs(flat) == []
    short = _hill_payload(flat_m=500.0, climb_m=300.0, gradient_pct=8.0, tail_m=200.0)
    assert detect_climbs(short) == []


def test_rebuild_climbs_matches_repeated_hill_and_keeps_name(tmp_path):
    db = tmp_path / "climbs.db"
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", _hill_payload(), strava_id=1)
    # Même montée, départ décalé de ~55 m : doit matcher le même segment.
    _seed_activity(
        db,
        2,
        "2026-04-12T08:00:00Z",
        _hill_payload(start_lat=48.0005),
        strava_id=2,
    )

    report = rebuild_climbs(db_path=db)
    assert report["segments_created"] == 1
    assert report["efforts_written"] == 2

    stats = climb_report(db_path=db)
    assert stats["available"] is True
    segment = stats["segments"][0]
    assert segment["efforts"] == 2
    assert segment["best_sec"] is not None and segment["avg_sec"] is not None
    assert segment["by_year"] == [{"year": "2026", "efforts": 2, "best_sec": segment["best_sec"]}]

    rename_climb(segment["id"], "Col de test", db_path=db)
    rebuild_climbs(db_path=db)
    stats = climb_report(db_path=db)
    assert stats["segments"][0]["name"] == "Col de test"
    assert stats["segments"][0]["efforts"] == 2
    assert stats["unnamed_segments"] == 0


def test_rebuild_counts_two_passages_in_same_activity(tmp_path):
    """Une sortie peut grimper deux fois la même montée (aller-retour)."""
    first = _hill_payload()
    second = _hill_payload()
    time_offset = first["time"][-1] + STEP_SEC
    distance_offset = first["distance"][-1] + SPEED_MPS * STEP_SEC
    two_pass = {
        "time": first["time"] + [time_offset + t for t in second["time"]],
        "distance": first["distance"] + [distance_offset + d for d in second["distance"]],
        "altitude": first["altitude"] + second["altitude"],
        "lat": first["lat"] + second["lat"],
        "lng": first["lng"] + second["lng"],
    }

    db = tmp_path / "climbs.db"
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", two_pass, strava_id=1)

    report = rebuild_climbs(db_path=db)
    assert report["segments_created"] == 1
    assert report["efforts_written"] == 2

    stats = climb_report(db_path=db)
    assert stats["segments"][0]["efforts"] == 2
    assert len(climb_detail(stats["segments"][0]["id"], db_path=db)["efforts_list"]) == 2


def test_rebuild_climbs_separates_distant_hills(tmp_path):
    db = tmp_path / "climbs.db"
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", _hill_payload(), strava_id=1)
    # Départ ~1,1 km plus loin : segment distinct.
    _seed_activity(db, 2, "2026-04-12T08:00:00Z", _hill_payload(start_lat=48.01), strava_id=2)

    report = rebuild_climbs(db_path=db)
    assert report["segments_created"] == 2
    stats = climb_report(db_path=db)
    assert stats["total_segments"] == 2


def test_delete_activity_updates_climb_counts(tmp_path):
    db = tmp_path / "climbs.db"
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", _hill_payload(), strava_id=1)
    _seed_activity(db, 2, "2026-04-12T08:00:00Z", _hill_payload(), strava_id=2)
    rebuild_climbs(db_path=db)

    assert delete_activity(1, db_path=db) is True
    stats = climb_report(db_path=db)
    assert stats["segments"][0]["efforts"] == 1


def test_climb_report_name_filter_and_empty_db(tmp_path):
    empty = tmp_path / "empty.db"
    init_db(empty)
    assert climb_report(db_path=empty)["available"] is False

    db = tmp_path / "climbs.db"
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", _hill_payload(), strava_id=1)
    rebuild_climbs(db_path=db)
    segment_id = climb_report(db_path=db)["segments"][0]["id"]
    rename_climb(segment_id, "Haut-Koenigsbourg", db_path=db)

    found = climb_report(name="koenigs", db_path=db)
    assert found["available"] is True
    assert found["segments"][0]["name"] == "Haut-Koenigsbourg"
    assert found["segments"][0]["efforts"] == 1

    missing = climb_report(name="Ventoux", db_path=db)
    assert missing["available"] is False
    assert "unnamed_segments" in missing


def test_get_climb_stats_tool_dispatch(tmp_path, monkeypatch):
    db = tmp_path / "climbs.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    init_db(db)
    _seed_activity(db, 1, "2026-04-05T08:00:00Z", _hill_payload(), strava_id=1)
    rebuild_climbs(db_path=db)
    segment_id = climb_report(db_path=db)["segments"][0]["id"]
    rename_climb(segment_id, "Col de test", db_path=db)

    out = dispatch("get_climb_stats", {"name": "Col de test"})
    assert out["available"] is True
    assert out["segments"][0]["name"] == "Col de test"
    assert out["segments"][0]["efforts"] == 1
    assert out["segments"][0]["best_sec"] is not None

    empty = dispatch("get_climb_stats", {"name": "Inconnu"})
    assert empty["available"] is False
