"""Tests pour les tools exposés au coach LLM."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from domestique_ai.ingestion.db import init_db
from domestique_ai.llm.tools import (
    TOOL_SCHEMAS,
    dispatch,
    get_activity_details,
    get_objective,
    get_planned_workout,
    get_recent_activities,
    get_training_load_state,
    get_zone_distribution,
    propose_workout,
)


@pytest.fixture
def freeze_today(monkeypatch):
    """Fixe la date utilisée par les tools à 2026-04-30 (dernière activité seedée)."""
    monkeypatch.setattr(
        "domestique_ai.llm.tools._today",
        lambda: dt.date(2026, 4, 30),
    )


def _seed_activities(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = [
            (
                1,
                "2026-04-25T08:00:00Z",
                3600,
                145,
                175,
                None,
                200,
                30000,
                80,
                600,
                1800,
                900,
                240,
                60,
            ),
            (
                2,
                "2026-04-27T08:00:00Z",
                5400,
                150,
                180,
                None,
                350,
                60000,
                110,
                900,
                2700,
                1200,
                500,
                100,
            ),
            (3, "2026-04-30T08:00:00Z", 1800, 130, 160, None, 100, 15000, 40, 600, 900, 300, 0, 0),
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
        "get_planned_workout",
        "propose_workout",
        "propose_workout_today",
        "review_week",
        "find_similar_activities",
    }
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]


def test_propose_workout_today_dispatchable(tmp_path, monkeypatch):
    """Le tool est dispatchable et renvoie le contrat attendu."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "today.db"))
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "avail.yaml"))
    init_db(tmp_path / "today.db")
    # Pas d'availability → pas de contrainte, TSB=0 par défaut → endurance.
    out = dispatch("propose_workout_today", {})
    assert "rest_day" in out
    assert out["rest_day"] is False
    assert out["workout"]["kind"] in {"recovery", "endurance", "tempo", "intervals"}


def test_review_week_dispatchable(tmp_path, monkeypatch):
    """Le tool review_week renvoie le rapport de semaine sans erreur."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "review.db"))
    init_db(tmp_path / "review.db")
    out = dispatch("review_week", {})
    assert "error" not in out
    assert out["week_key"] is not None
    assert "compliance" in out
    assert out["active_plan_id"] is None


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


def test_get_recent_activities_filters_window(seeded_db, freeze_today):
    out = get_recent_activities(days=3)
    assert out["count"] == 2  # 2026-04-27 et 2026-04-30 dans la fenêtre 3j
    assert out["as_of"] == "2026-04-30"
    activity = out["activities"][0]
    assert "hr_zones_sec" in activity
    assert set(activity["hr_zones_sec"]) == {"z1", "z2", "z3", "z4", "z5"}


def test_get_recent_activities_ignores_old_when_unsynced(seeded_db, monkeypatch):
    """
    Régression CR-006 : si l'utilisateur n'a pas synchronisé depuis longtemps,
    « les 7 derniers jours » doit renvoyer 0 activité (et non les 7 jours
    précédant la dernière sync, parfois vieille de plusieurs semaines).
    """
    monkeypatch.setattr(
        "domestique_ai.llm.tools._today",
        lambda: dt.date(2026, 5, 21),
    )
    out = get_recent_activities(days=7)
    assert out["count"] == 0
    assert out["as_of"] == "2026-05-21"


def test_get_zone_distribution_aggregates(seeded_db, freeze_today):
    dist = get_zone_distribution(days=10)
    assert dist["activities_with_zones"] == 3
    assert dist["as_of"] == "2026-04-30"
    z2 = dist["distribution"]["z2"]
    assert z2["seconds"] == 1800 + 2700 + 900  # somme z2 des 3 lignes
    assert z2["minutes"] == pytest.approx(z2["seconds"] / 60, abs=0.1)
    assert sum(v["share_pct"] for v in dist["distribution"].values()) == pytest.approx(
        100.0, abs=0.5
    )


def test_get_zone_distribution_skips_null_zones(tmp_path, monkeypatch):
    db_path = tmp_path / "partial.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "domestique_ai.llm.tools._today",
        lambda: dt.date(2026, 4, 30),
    )
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
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "missing.yaml"))
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
    details = get_activity_details(external_id=2)
    assert details["available"] is True
    assert details["distance_km"] == 60.0
    assert details["hr_zones_sec"]["z2"] == 2700


def test_get_activity_details_unknown_id(seeded_db):
    details = get_activity_details(external_id=999)
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


def test_dispatch_routes_to_correct_function(seeded_db, freeze_today):
    result = dispatch("get_recent_activities", {"days": 7})
    assert result["count"] >= 1


def test_generate_training_plan_with_objective(seeded_db, tmp_path, monkeypatch):
    obj_path = tmp_path / "objective.yaml"
    obj_path.write_text("type: cyclosportive\ndate: 2026-09-01\ndistance_km: 100\n")
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(obj_path))

    result = dispatch("generate_training_plan", {"sessions_per_week": 4})
    assert result["available"] is True
    assert result["sessions_count"] > 0
    assert result["target_date"] == "2026-09-01"
    assert isinstance(result["weekly"], list) and result["weekly"]
    assert result["peak_week"]["tss"] >= 0
    assert "first_session" in result and "structure" in result["first_session"]


def test_generate_training_plan_fallback_no_objective(seeded_db, tmp_path, monkeypatch):
    # Pas de fichier objective.yaml → fallback 4 semaines.
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "missing.yaml"))
    result = dispatch("generate_training_plan", {"sessions_per_week": 3})
    assert result["available"] is True
    assert result["sessions_count"] > 0
    assert result["target_date"] is None


def test_generate_training_plan_invalid_sessions(seeded_db):
    result = dispatch("generate_training_plan", {"sessions_per_week": 1})
    assert result["available"] is False


def test_generate_training_plan_uses_availability_when_present(seeded_db, tmp_path, monkeypatch):
    avail_path = tmp_path / "availability.yaml"
    avail_path.write_text(
        "days:\n"
        "  wednesday:\n    max_duration_min: 90\n    context: indoor\n"
        "  thursday:\n    max_duration_min: 90\n    context: indoor\n"
        "  saturday:\n    max_duration_min: 240\n    context: outdoor\n"
        "  sunday:\n    max_duration_min: 240\n    context: outdoor\n"
    )
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(avail_path))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "missing.yaml"))

    result = dispatch("generate_training_plan", {"sessions_per_week": 4})
    assert result["available"] is True
    assert result["availability_loaded"] is True
    assert set(result["days_used"]) <= {
        "wednesday",
        "thursday",
        "saturday",
        "sunday",
    }
    # Aucun jour Lundi/Mardi/Vendredi → exclusion vérifiée.
    assert "monday" not in result["days_used"]
    assert "friday" not in result["days_used"]


def test_generate_training_plan_falls_back_without_availability(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(tmp_path / "missing-avail.yaml"))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "missing-obj.yaml"))
    result = dispatch("generate_training_plan", {"sessions_per_week": 4})
    assert result["available"] is True
    assert result["availability_loaded"] is False
    # Comportement legacy : on retombe sur Lun/Mer/Ven/Dim au moins en partie.
    assert "monday" in result["days_used"] or "wednesday" in result["days_used"]


def test_generate_training_plan_invalid_availability_yaml_returns_error(
    seeded_db, tmp_path, monkeypatch
):
    bad = tmp_path / "availability.yaml"
    bad.write_text("days:\n  mondai:\n    max_duration_min: 60\n    context: indoor\n")
    monkeypatch.setenv("DOMESTIQUE_AI_AVAILABILITY_PATH", str(bad))

    result = dispatch("generate_training_plan", {"sessions_per_week": 4})
    assert result["available"] is False
    assert "availability.yaml" in result["reason"]


# ---- get_planned_workout -----------------------------------------------------


def _make_workout(
    date: str,
    kind: str = "endurance",
    target_zone: str = "z2",
    duration_min: int = 90,
    estimated_tss: float = 70.0,
):
    from domestique_ai.processing.plan_builder import Workout, WorkoutStep

    return Workout(
        date=date,
        name=f"{kind.title()} {duration_min}min",
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone=target_zone,
        structure=[WorkoutStep(phase="active", zone=target_zone, duration_sec=duration_min * 60)],
        estimated_tss=estimated_tss,
    )


@pytest.fixture
def planned_db(tmp_path, monkeypatch):
    db_path = tmp_path / "planned.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db_path))
    return db_path


def test_get_planned_workout_match(planned_db):
    import datetime as dt

    from domestique_ai.llm.plan_storage import save_plan

    plan = [
        _make_workout("2026-05-04", kind="endurance", target_zone="z2"),
        _make_workout(
            "2026-05-08", kind="threshold", target_zone="z4", duration_min=75, estimated_tss=95.0
        ),
        _make_workout(
            "2026-05-11", kind="recovery", target_zone="z1", duration_min=45, estimated_tss=25.0
        ),
    ]
    save_plan(
        plan,
        target_date=dt.date(2026, 6, 1),
        target_event_type="cyclosportive",
        sessions_per_week=4,
    )

    out = get_planned_workout(date="2026-05-08")
    assert out["available"] is True
    assert out["plan_target_event_type"] == "cyclosportive"
    assert out["planned_workout"]["kind"] == "threshold"
    assert out["planned_workout"]["target_zone"] == "z4"
    assert out["planned_workout"]["duration_min"] == 75
    assert out["total_plans_considered"] == 1


def test_get_planned_workout_rest_day(planned_db):
    import datetime as dt

    from domestique_ai.llm.plan_storage import save_plan

    plan = [
        _make_workout("2026-05-04"),
        _make_workout("2026-05-08"),
        _make_workout("2026-05-11"),
    ]
    save_plan(plan, target_date=dt.date(2026, 6, 1), sessions_per_week=4)

    # 2026-05-06 est dans la fenêtre [05-04 ; 05-11] mais aucune séance ce jour.
    out = get_planned_workout(date="2026-05-06")
    assert out["available"] is True
    assert out["planned_workout"] is None
    assert "repos" in out["note"].lower()


def test_get_planned_workout_outside_window(planned_db):
    import datetime as dt

    from domestique_ai.llm.plan_storage import save_plan

    plan = [
        _make_workout("2026-05-04"),
        _make_workout("2026-05-11"),
    ]
    save_plan(plan, target_date=dt.date(2026, 6, 1), sessions_per_week=4)

    out = get_planned_workout(date="2026-04-30")
    assert out["available"] is False
    assert out["total_plans_considered"] == 1


def test_get_planned_workout_multi_plans_picks_most_recent(planned_db):
    import datetime as dt

    from domestique_ai.llm.plan_storage import save_plan

    # Plan 1 : 04 → 11 mai, séance Z2 le 08.
    plan_v1 = [
        _make_workout("2026-05-04"),
        _make_workout("2026-05-08", kind="endurance", target_zone="z2"),
        _make_workout("2026-05-11"),
    ]
    save_plan(plan_v1, target_date=dt.date(2026, 6, 1), sessions_per_week=4)
    # Plan 2 (régénération) : même fenêtre, mais le 08 devient Z4.
    plan_v2 = [
        _make_workout("2026-05-04"),
        _make_workout("2026-05-08", kind="threshold", target_zone="z4"),
        _make_workout("2026-05-11"),
    ]
    save_plan(plan_v2, target_date=dt.date(2026, 6, 8), sessions_per_week=4)

    out = get_planned_workout(date="2026-05-08")
    assert out["available"] is True
    # Le plan le plus récent (id le plus haut) gagne.
    assert out["planned_workout"]["target_zone"] == "z4"
    assert out["plan_target_date"] == "2026-06-08"
    assert out["total_plans_considered"] == 2


def test_get_planned_workout_no_plans(planned_db):
    out = get_planned_workout(date="2026-05-08")
    assert out["available"] is False
    assert out["total_plans_considered"] == 0


def test_get_planned_workout_invalid_date(planned_db):
    out = get_planned_workout(date="not-a-date")
    assert out["available"] is False
    assert "invalide" in out["reason"]
