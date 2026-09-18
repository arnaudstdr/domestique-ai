"""Tests unitaires pour l'agrégat volume vélo hebdomadaire."""

from __future__ import annotations

import datetime as dt
import sqlite3

from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.trends import _week_start, _weeks_in_range, get_weekly_volume


def _seed_activities(db_path, rows: list[dict]) -> None:
    """Insère des activités minimales dans une DB tmp (training_load déjà calculé)."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for row in rows:
            conn.execute(
                "INSERT INTO activities (strava_id, date, duration, "
                "elevation_gain, distance, training_load, sport_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["strava_id"],
                    row["date"],
                    row.get("duration", 3600),
                    row.get("elevation_gain", 0),
                    row.get("distance", 0),
                    row.get("training_load", 0.0),
                    row.get("sport_type", "Ride"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


TODAY = dt.date(2026, 5, 20)  # mercredi, semaine ISO du lundi 2026-05-18


def test_weeks_in_range_is_continuous():
    weeks = _weeks_in_range(dt.date(2026, 5, 4), TODAY)
    assert weeks == [
        dt.date(2026, 5, 4),
        dt.date(2026, 5, 11),
        dt.date(2026, 5, 18),
    ]


def test_week_start_monday():
    assert _week_start(dt.date(2026, 5, 20)) == dt.date(2026, 5, 18)


def test_sums_activities_within_same_week(tmp_path):
    db = tmp_path / "x.db"
    _seed_activities(
        db,
        [
            {"strava_id": 1, "date": "2026-05-18T08:00:00Z", "distance": 30000, "duration": 3600},
            {"strava_id": 2, "date": "2026-05-20T08:00:00Z", "distance": 20000, "duration": 1800},
        ],
    )
    result = get_weekly_volume(weeks=3, db_path=db, today=TODAY)
    weeks = result["weeks"]
    assert len(weeks) == 3
    assert weeks[-1]["week_starting"] == "2026-05-18"
    assert weeks[-1]["distance_km"] == 50.0
    assert weeks[-1]["duration_sec"] == 5400
    assert weeks[-1]["sessions"] == 2


def test_zero_fills_empty_weeks(tmp_path):
    db = tmp_path / "x.db"
    _seed_activities(
        db,
        [{"strava_id": 1, "date": "2026-05-20T08:00:00Z", "distance": 10000}],
    )
    weeks = get_weekly_volume(weeks=4, db_path=db, today=TODAY)["weeks"]
    assert [w["distance_km"] for w in weeks] == [0.0, 0.0, 0.0, 10.0]
    assert [w["week_starting"] for w in weeks] == [
        "2026-04-27",
        "2026-05-04",
        "2026-05-11",
        "2026-05-18",
    ]


def test_excludes_non_ride(tmp_path):
    db = tmp_path / "x.db"
    _seed_activities(
        db,
        [
            {"strava_id": 1, "date": "2026-05-20T08:00:00Z", "distance": 10000, "sport_type": "Ride"},
            {"strava_id": 2, "date": "2026-05-20T09:00:00Z", "distance": 99999, "sport_type": "Run"},
        ],
    )
    weeks = get_weekly_volume(weeks=1, db_path=db, today=TODAY)["weeks"]
    assert weeks[-1]["distance_km"] == 10.0
    assert weeks[-1]["sessions"] == 1


def test_sunday_belongs_to_previous_week(tmp_path):
    db = tmp_path / "x.db"
    _seed_activities(
        db,
        [{"strava_id": 1, "date": "2026-05-17T08:00:00Z", "distance": 42000}],
    )
    weeks = get_weekly_volume(weeks=2, db_path=db, today=TODAY)["weeks"]
    assert weeks[0]["week_starting"] == "2026-05-11"
    assert weeks[0]["distance_km"] == 42.0
    assert weeks[1]["distance_km"] == 0.0


def test_window_excludes_older_activities(tmp_path):
    db = tmp_path / "x.db"
    _seed_activities(
        db,
        [{"strava_id": 1, "date": "2026-01-05T08:00:00Z", "distance": 50000}],
    )
    weeks = get_weekly_volume(weeks=2, db_path=db, today=TODAY)["weeks"]
    assert all(w["distance_km"] == 0.0 for w in weeks)
