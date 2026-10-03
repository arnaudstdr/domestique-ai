"""Tests des agrégats par type de sortie (processing.activity_stats)."""

from __future__ import annotations

import datetime as dt
import sqlite3

from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.activity_classify import infer_kind_from_zones, sport_bucket
from domestique_ai.processing.activity_stats import get_activity_mix_stats

TODAY = dt.date(2026, 4, 30)


def _seed(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO activities (strava_id, date, duration, avg_heart_rate, "
            "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time, sport_type, "
            "distance, elevation_gain, training_load) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # endurance : aucun z4/z5, z3 sous 30 %.
                (
                    1,
                    "2026-04-05T08:00:00Z",
                    3600,
                    140,
                    600,
                    1800,
                    900,
                    240,
                    60,
                    "Ride",
                    40000,
                    300,
                    80,
                ),
                # tempo : z3 ≥ 30 % et z4+z5 < 20 %.
                (
                    2,
                    "2026-04-12T08:00:00Z",
                    3600,
                    155,
                    600,
                    1000,
                    1500,
                    400,
                    100,
                    "Ride",
                    45000,
                    400,
                    110,
                ),
                # intervals : z4+z5 ≥ 20 %, home trainer.
                (
                    3,
                    "2026-04-19T08:00:00Z",
                    3600,
                    165,
                    200,
                    400,
                    900,
                    1500,
                    600,
                    "VirtualRide",
                    30000,
                    100,
                    120,
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_sport_bucket_mapping():
    assert sport_bucket("VirtualRide") == "indoor"
    assert sport_bucket("Ride") == "outdoor"
    assert sport_bucket("Run") == "other"
    assert sport_bucket(None) == "other"


def test_infer_kind_from_zones_branches():
    assert infer_kind_from_zones({}, None) is None
    assert infer_kind_from_zones({"z1": 0, "z2": 0}, None) is None
    assert infer_kind_from_zones({"z4": 300, "z5": 100, "z1": 600}, 150) == "intervals"
    assert infer_kind_from_zones({"z3": 400, "z2": 600}, 150) == "tempo"
    assert infer_kind_from_zones({"z1": 800, "z2": 200}, 120) == "recovery"
    assert infer_kind_from_zones({"z1": 800, "z2": 200}, 145) == "endurance"


def test_mix_stats_kind_and_monthly(tmp_path):
    db = tmp_path / "stats.db"
    _seed(db)
    out = get_activity_mix_stats(
        days=60, group_by="kind", include_monthly=True, db_path=db, today=TODAY
    )
    assert out["group_by"] == "kind"
    assert out["total_sessions"] == 3
    by_type = {row["type"]: row for row in out["by_type"]}
    assert set(by_type) == {"endurance", "tempo", "intervals"}
    assert by_type["intervals"]["avg_duration_min"] == 60.0
    assert [(row["month"], row["type"]) for row in out["monthly"]] == [
        ("2026-04", "endurance"),
        ("2026-04", "intervals"),
        ("2026-04", "tempo"),
    ]


def test_mix_stats_indoor_and_window(tmp_path):
    db = tmp_path / "stats.db"
    _seed(db)
    out = get_activity_mix_stats(days=60, group_by="indoor", db_path=db, today=TODAY)
    by_type = {row["type"]: row for row in out["by_type"]}
    assert by_type["indoor"]["sessions"] == 1
    assert by_type["outdoor"]["sessions"] == 2

    # Fenêtre courte : days=10 → start = 20/04, toutes les activités seedées
    # (≤ 19/04) tombent hors fenêtre.
    narrow = get_activity_mix_stats(days=10, group_by="kind", db_path=db, today=TODAY)
    assert narrow["total_sessions"] == 0


def test_mix_stats_invalid_group_by_falls_back(tmp_path):
    db = tmp_path / "stats.db"
    _seed(db)
    out = get_activity_mix_stats(days=60, group_by="wat", db_path=db, today=TODAY)
    assert out["group_by"] == "sport"
    assert "by_sport" in out
