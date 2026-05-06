"""Tests pour les tools exposés au coach LLM."""

from __future__ import annotations

import sqlite3

import pytest

from domestique_ai.ingestion.strava import init_db
from domestique_ai.llm.tools import (
    TOOL_SCHEMAS,
    dispatch,
    get_activity_details,
    get_objective,
    get_recent_activities,
    get_training_load_state,
    get_zone_distribution,
    propose_workout,
)


def _seed_activities(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = [
            (1, "2026-04-25T08:00:00Z", 3600, 145, 175, None, 200, 30000, 80,
             600, 1800, 900, 240, 60),
            (2, "2026-04-27T08:00:00Z", 5400, 150, 180, None, 350, 60000, 110,
             900, 2700, 1200, 500, 100),
            (3, "2026-04-30T08:00:00Z", 1800, 130, 160, None, 100, 15000, 40,
             600, 900, 300, 0, 0),
        ]
        conn.executemany(
            "INSERT INTO activities ("
            "strava_id, date, duration, avg_heart_rate, max_heart_rate, "
            "avg_power, elevation_gain, distance, training_load, "
            "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "tools.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    _seed_activities(db_path)
    return db_path


def test_tool_schemas_have_required_shape():
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert names == {
        "get_training_load_state",
        "get_recent_activities",
        "get_zone_distribution",
        "get_objective",
        "get_activity_details",
        "get_morning_trends",
        "get_overtraining_signals",
        "generate_training_plan",
        "propose_workout",
    }
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]


def test_get_training_load_state_returns_curve(seeded_db):
    state = get_training_load_state()
    assert state["available"] is True
    assert state["zone"] in {"Frais", "Optimal", "Fatigué", "Surentraîné"}
    assert state["ctl"] >= 0
    assert "interpretation" in state


def test_get_training_load_state_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "empty.db"))
    init_db(tmp_path / "empty.db")
    state = get_training_load_state()
    assert state["available"] is False


def test_get_recent_activities_filters_window(seeded_db):
    out = get_recent_activities(days=3)
    assert out["count"] == 2  # 2026-04-27 et 2026-04-30 dans la fenêtre 3j
    activity = out["activities"][0]
    assert "hr_zones_sec" in activity
    assert set(activity["hr_zones_sec"]) == {"z1", "z2", "z3", "z4", "z5"}


def test_get_zone_distribution_aggregates(seeded_db):
    dist = get_zone_distribution(days=10)
    assert dist["activities_with_zones"] == 3
    z2 = dist["distribution"]["z2"]
    assert z2["seconds"] == 1800 + 2700 + 900  # somme z2 des 3 lignes
    assert z2["minutes"] == pytest.approx(z2["seconds"] / 60, abs=0.1)
    assert sum(v["share_pct"] for v in dist["distribution"].values()) == \
        pytest.approx(100.0, abs=0.5)


def test_get_zone_distribution_skips_null_zones(tmp_path, monkeypatch):
    db_path = tmp_path / "partial.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration, "
            "avg_heart_rate, training_load, hr_z1_time) "
            "VALUES (1, '2026-04-25T08:00:00Z', 3600, 140, 80, 600)"
        )
        # Activité sans toutes les zones renseignées
        conn.execute(
            "INSERT INTO activities (strava_id, date, duration, "
            "avg_heart_rate, training_load) "
            "VALUES (2, '2026-04-26T08:00:00Z', 3600, 140, 80)"
        )
        conn.commit()
    finally:
        conn.close()

    dist = get_zone_distribution(days=10)
    # Aucune activité n'a TOUTES les zones renseignées (z1 only sur la 1ère)
    assert dist["activities_with_zones"] == 0


def test_get_objective_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH",
                       str(tmp_path / "missing.yaml"))
    out = get_objective()
    assert out["available"] is False


def test_get_objective_present(tmp_path, monkeypatch):
    path = tmp_path / "obj.yaml"
    path.write_text("type: course\ndate: 2026-06-01\ndistance_km: 42\n")
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))
    out = get_objective()
    assert out["available"] is True
    assert out["objective"]["distance_km"] == 42


def test_get_activity_details_known_id(seeded_db):
    details = get_activity_details(strava_id=2)
    assert details["available"] is True
    assert details["distance_km"] == 60.0
    assert details["hr_zones_sec"]["z2"] == 2700


def test_get_activity_details_unknown_id(seeded_db):
    details = get_activity_details(strava_id=999)
    assert details["available"] is False


def test_propose_workout_endurance():
    out = propose_workout(target_zone="z2", duration_min=90)
    assert out["available"] is True
    assert out["kind"] == "endurance"
    zones = {phase.get("zone") for phase in out["structure"] if "zone" in phase}
    assert "z2" in zones


def test_propose_workout_threshold():
    out = propose_workout(target_zone="z4", duration_min=60)
    assert out["available"] is True
    assert out["kind"] == "intervals_threshold"


def test_propose_workout_invalid_zone():
    out = propose_workout(target_zone="z99", duration_min=60)
    assert out["available"] is False


def test_propose_workout_invalid_duration():
    out = propose_workout(target_zone="z2", duration_min=0)
    assert out["available"] is False


def test_dispatch_unknown_tool():
    result = dispatch("unknown_tool", {})
    assert "error" in result


def test_dispatch_with_invalid_args():
    # propose_workout exige target_zone et duration_min
    result = dispatch("propose_workout", {})
    assert "error" in result


def test_dispatch_routes_to_correct_function(seeded_db):
    result = dispatch("get_recent_activities", {"days": 7})
    assert result["count"] >= 1


def test_generate_training_plan_with_objective(seeded_db, tmp_path, monkeypatch):
    obj_path = tmp_path / "objective.yaml"
    obj_path.write_text(
        "type: cyclosportive\ndate: 2026-09-01\ndistance_km: 100\n"
    )
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(obj_path))

    result = dispatch("generate_training_plan", {"sessions_per_week": 4})
    assert result["available"] is True
    assert result["sessions_count"] > 0
    assert result["target_date"] == "2026-09-01"
    assert isinstance(result["weekly"], list) and result["weekly"]
    assert result["peak_week"]["tss"] >= 0
    assert "first_session" in result and "structure" in result["first_session"]


def test_generate_training_plan_fallback_no_objective(seeded_db, tmp_path,
                                                     monkeypatch):
    # Pas de fichier objective.yaml → fallback 4 semaines.
    monkeypatch.setenv(
        "DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "missing.yaml")
    )
    result = dispatch("generate_training_plan", {"sessions_per_week": 3})
    assert result["available"] is True
    assert result["sessions_count"] > 0
    assert result["target_date"] is None


def test_generate_training_plan_invalid_sessions(seeded_db):
    result = dispatch("generate_training_plan", {"sessions_per_week": 1})
    assert result["available"] is False
