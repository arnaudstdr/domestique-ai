"""Tests de la recherche d'activités similaires."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.similar import find_similar_activities


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "similar.db"
    init_db(path)
    return path


def _save(
    db_path: Path,
    strava_id: int,
    *,
    date: str = "2026-05-01T08:00:00Z",
    distance_m: float = 50_000,
    elevation_m: float = 500,
    duration_sec: int = 7200,
    sport_type: str = "Ride",
    avg_power: float | None = 220.0,
    training_load: float | None = 80.0,
    avg_heart_rate: float | None = 145.0,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration, avg_heart_rate, "
            "avg_power, elevation_gain, distance, training_load, sport_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                strava_id,
                date,
                duration_sec,
                avg_heart_rate,
                avg_power,
                elevation_m,
                distance_m,
                training_load,
                sport_type,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- Cas d'erreur -----------------------------------------------------


def test_returns_unavailable_when_activity_not_found(db_path: Path):
    result = find_similar_activities(9999, db_path=db_path)
    assert result["available"] is False
    assert "introuvable" in result["reason"]


def test_returns_unavailable_for_very_short_activity(db_path: Path):
    _save(db_path, 1, distance_m=2_000)  # 2 km, sous le plancher de 5 km
    result = find_similar_activities(1, db_path=db_path)
    assert result["available"] is False
    assert "courte" in result["reason"].lower()


# ---------- Match nominal ----------------------------------------------------


def test_finds_identical_loop_repeated_each_week(db_path: Path, monkeypatch):
    """Une boucle hebdomadaire (même distance + dénivelé) doit être détectée."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    # 4 sorties identiques sur 4 semaines.
    for i, day in enumerate(["01", "08", "15", "22"]):
        _save(
            db_path,
            10 + i,
            date=f"2026-05-{day}T08:00:00Z",
            distance_m=50_000,
            elevation_m=500,
            training_load=80.0 + i,
        )
    result = find_similar_activities(10, db_path=db_path)
    assert result["available"] is True
    assert len(result["matches"]) == 3
    # Tri date descendante.
    assert [m["date"][:10] for m in result["matches"]] == ["2026-05-22", "2026-05-15", "2026-05-08"]


def test_matches_within_5pct_distance_tolerance(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500)
    # +4 % en distance → dans la tolérance.
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=52_000,
        elevation_m=500,
    )
    # +8 % en distance → hors tolérance.
    _save(
        db_path,
        3,
        date="2026-05-15T08:00:00Z",
        distance_m=54_000,
        elevation_m=500,
    )
    result = find_similar_activities(1, db_path=db_path)
    ids = [m["external_id"] for m in result["matches"]]
    assert 2 in ids
    assert 3 not in ids


def test_matches_within_10pct_elevation_tolerance(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500)
    # +8 % en dénivelé → dans la tolérance.
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=50_000,
        elevation_m=540,
    )
    # +20 % en dénivelé → hors tolérance.
    _save(
        db_path,
        3,
        date="2026-05-15T08:00:00Z",
        distance_m=50_000,
        elevation_m=600,
    )
    result = find_similar_activities(1, db_path=db_path)
    ids = [m["external_id"] for m in result["matches"]]
    assert 2 in ids
    assert 3 not in ids


# ---------- Bucket de sport --------------------------------------------------


def test_does_not_match_indoor_with_outdoor(db_path: Path, monkeypatch):
    """Une sortie route ne doit jamais matcher un home trainer."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500, sport_type="Ride")
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=50_000,
        elevation_m=500,
        sport_type="VirtualRide",
    )
    result = find_similar_activities(1, db_path=db_path)
    assert all(m["external_id"] != 2 for m in result["matches"])


def test_matches_within_same_outdoor_bucket(db_path: Path, monkeypatch):
    """Gravel et MTB doivent matcher Ride (mêmes profils possibles)."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500, sport_type="Ride")
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=51_000,
        elevation_m=510,
        sport_type="GravelRide",
    )
    _save(
        db_path,
        3,
        date="2026-05-15T08:00:00Z",
        distance_m=49_000,
        elevation_m=490,
        sport_type="MountainBikeRide",
    )
    result = find_similar_activities(1, db_path=db_path)
    ids = [m["external_id"] for m in result["matches"]]
    assert 2 in ids
    assert 3 in ids


def test_matches_within_indoor_bucket(db_path: Path, monkeypatch):
    """Deux séances home trainer comparables doivent matcher."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(
        db_path,
        1,
        distance_m=30_000,
        elevation_m=0,
        sport_type="VirtualRide",
    )
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=31_000,
        elevation_m=0,
        sport_type="VirtualRide",
    )
    result = find_similar_activities(1, db_path=db_path)
    assert any(m["external_id"] == 2 for m in result["matches"])


def test_other_sport_does_not_match(db_path: Path, monkeypatch):
    """Une course à pied ne doit pas matcher une sortie vélo."""
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500, sport_type="Ride")
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=50_000,
        elevation_m=500,
        sport_type="Run",
    )
    result = find_similar_activities(1, db_path=db_path)
    assert all(m["external_id"] != 2 for m in result["matches"])


# ---------- Computed fields --------------------------------------------------


def test_delta_pct_computed_relative_to_reference(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(
        db_path,
        1,
        distance_m=50_000,
        elevation_m=500,
        training_load=80.0,
        avg_power=200.0,
        duration_sec=7200,
    )
    # +10 % TSS, +5 % power, -10 % durée.
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=50_000,
        elevation_m=500,
        training_load=88.0,
        avg_power=210.0,
        duration_sec=6480,
    )
    result = find_similar_activities(1, db_path=db_path)
    match = next(m for m in result["matches"] if m["external_id"] == 2)
    assert match["tss_delta_pct"] == pytest.approx(10.0, abs=0.1)
    assert match["power_delta_pct"] == pytest.approx(5.0, abs=0.1)
    assert match["duration_delta_pct"] == pytest.approx(-10.0, abs=0.1)


def test_delta_pct_returns_none_when_reference_has_no_power(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500, avg_power=None)
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=50_000,
        elevation_m=500,
        avg_power=200.0,
    )
    result = find_similar_activities(1, db_path=db_path)
    match = next(m for m in result["matches"] if m["external_id"] == 2)
    assert match["power_delta_pct"] is None


# ---------- Tri + limit ------------------------------------------------------


def test_results_sorted_by_date_desc(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    dates = ["2026-04-01", "2026-05-01", "2026-03-01", "2026-06-01"]
    _save(db_path, 1, date="2026-07-01T08:00:00Z", distance_m=50_000, elevation_m=500)
    for i, date in enumerate(dates):
        _save(
            db_path,
            100 + i,
            date=f"{date}T08:00:00Z",
            distance_m=50_000,
            elevation_m=500,
        )
    result = find_similar_activities(1, db_path=db_path)
    dates_returned = [m["date"][:10] for m in result["matches"]]
    assert dates_returned == sorted(dates_returned, reverse=True)


def test_limit_caps_result_size(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500)
    for i in range(15):
        _save(
            db_path,
            100 + i,
            date=f"2026-05-{(i % 28) + 1:02d}T08:00:00Z",
            distance_m=50_000,
            elevation_m=500,
        )
    result = find_similar_activities(1, limit=5, db_path=db_path)
    assert len(result["matches"]) == 5


# ---------- Empty result -----------------------------------------------------


def test_no_similar_activity_returns_empty_list(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500)
    # Une activité avec un profil très différent.
    _save(
        db_path,
        2,
        date="2026-05-08T08:00:00Z",
        distance_m=120_000,
        elevation_m=2000,
    )
    result = find_similar_activities(1, db_path=db_path)
    assert result["available"] is True
    assert result["matches"] == []


# ---------- Index --------------------------------------------------------------


def test_index_on_distance_is_created(db_path: Path):
    """L'index utilisé pour le pré-filtre doit être créé à la première requête."""
    # Activité minimale pour qu'on ait quelque chose à interroger.
    _save(db_path, 1, distance_m=50_000, elevation_m=500)
    find_similar_activities(1, db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'activities'"
            )
        }
    finally:
        conn.close()
    assert "idx_activities_distance_elev" in names


# ---------- Reference payload --------------------------------------------------


def test_reference_payload_includes_bucket(db_path: Path, monkeypatch):
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    _save(db_path, 1, distance_m=50_000, elevation_m=500, sport_type="Ride")
    result = find_similar_activities(1, db_path=db_path)
    assert result["reference"]["sport_bucket"] == "outdoor"
    assert result["reference"]["distance_km"] == pytest.approx(50.0)
    assert result["criteria"]["distance_tolerance_pct"] == 5.0
