"""Tests du check du matin (go / alléger / repos, répercuté dans le plan)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import init_db
from domestique_ai.llm.daily_decision import evaluate_daily_decision
from domestique_ai.llm.plan_storage import get_day_decision, save_plan
from domestique_ai.processing.morning_metrics import save_morning_entry
from domestique_ai.processing.plan_builder import Workout


@pytest.fixture()
def ctx(tmp_path: Path) -> AthleteContext:
    db = tmp_path / "daily.db"
    init_db(db)
    return AthleteContext(
        db_path=db,
        profile_path=tmp_path / "profile.yaml",
        objective_path=tmp_path / "objective.yaml",
        availability_path=tmp_path / "availability.yaml",
        ftp=250.0,
        hr_rest=60.0,
        hr_max=185.0,
        sex="M",
        lthr_pct=0.88,
    )


def _today() -> _dt.date:
    return _dt.date.today()


def _plan_for(ctx: AthleteContext, today: _dt.date) -> int:
    plan = [
        Workout(
            date=today.isoformat(),
            name="Intervals",
            sport="cycling",
            kind="intervals",
            duration_min=60,
            target_zone="z4",
            estimated_tss=95.0,
            notes="Indoor (home trainer)",
        ),
        Workout(
            date=(today + _dt.timedelta(days=2)).isoformat(),
            name="Longue",
            sport="cycling",
            kind="endurance",
            duration_min=90,
            target_zone="z2",
            estimated_tss=82.5,
            notes="Outdoor",
        ),
    ]
    return save_plan(plan, db_path=ctx.db_path)


def _seed_morning(
    ctx: AthleteContext,
    today: _dt.date,
    *,
    baseline_readiness: int = 80,
    baseline_sleep: float = 7.5,
    **kwargs: object,
) -> None:
    # Baseline : 14 entrées avant aujourd'hui (HRV stable, FC repos stable).
    for i in range(14, 0, -1):
        day = (today - _dt.timedelta(days=i)).isoformat()
        save_morning_entry(
            day, hrv_ms=60.0, resting_hr=55.0, sleep_hours=baseline_sleep,
            readiness_score=baseline_readiness,
            db_path=ctx.db_path,
        )
    save_morning_entry(
        today.isoformat(),
        hrv_ms=kwargs.get("hrv_ms", 60.0),
        resting_hr=kwargs.get("resting_hr", 55.0),
        sleep_hours=kwargs.get("sleep_hours", baseline_sleep),
        readiness_score=kwargs.get("readiness_score", baseline_readiness),
        sleep_score=kwargs.get("sleep_score"),
        db_path=ctx.db_path,
    )


def test_go_when_all_good(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    _seed_morning(ctx, _today())
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False)
    assert result["decision"] == "go"
    assert result["workout"] is not None
    assert result["persisted"] is False
    assert result["workout"]["kind"] == "intervals"


def test_rest_when_readiness_low(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    # Baseline déjà basse (30) : chute à 25 → warning, pas critical → branche readiness < 30.
    _seed_morning(ctx, _today(), baseline_readiness=30, readiness_score=25)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "rest"
    assert result["workout"] is None
    assert "readiness" in result["reason"].lower()


def test_rest_when_short_sleep(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    _seed_morning(ctx, _today(), sleep_hours=4.2, readiness_score=70)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "rest"


def test_adjust_when_moderate_readiness(ctx: AthleteContext) -> None:
    pid = _plan_for(ctx, _today())
    # Baseline 55 → 45 : delta -18 % (warning, pas critical) + readiness 45 < 50 → adjust.
    _seed_morning(ctx, _today(), baseline_readiness=55, readiness_score=45)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False)
    assert result["decision"] == "adjust"
    assert result["workout"] is not None
    # Intervals planifié → tempo allégé.
    assert result["workout"]["kind"] == "tempo"
    assert result["workout"]["duration_min"] <= 60
    assert result["persisted"] is True
    decision = get_day_decision(pid, _today().isoformat(), db_path=ctx.db_path)
    assert decision is not None
    assert decision["decision"] == "adjusted"
    assert decision["decided_by"] == "daily_check"


def test_adjust_when_tsb_low(ctx: AthleteContext, monkeypatch: pytest.MonkeyPatch) -> None:
    # Pas d'historique d'activités → TSB 0. Forçons tsb bas via monkeypatch du
    # calcul : on insère des activités très chargées avant aujourd'hui.

    _plan_for(ctx, _today())
    _seed_morning(ctx, _today(), readiness_score=75)
    # Charge lourde les 10 derniers jours → TSB négatif.
    import sqlite3

    conn = sqlite3.connect(ctx.db_path)
    try:
        for i in range(10, 0, -1):
            conn.execute(
                "INSERT INTO activities (strava_id, date, duration, training_load, sport_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (i, (_today() - _dt.timedelta(days=i)).isoformat(), 3600, 90.0, "Ride"),
            )
        conn.commit()
    finally:
        conn.close()
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] in ("adjust", "rest")


def test_rest_when_morning_critical(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    # HRV en chute > 20 % → alerte critical (2 × seuil 10 %).
    _seed_morning(ctx, _today(), hrv_ms=40.0, readiness_score=80)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "rest"


def test_no_persist_when_no_plan_id(ctx: AthleteContext) -> None:
    # Aucun plan : pas de séance prévue → décision "go" (rien à décider).
    _seed_morning(ctx, _today())
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False)
    assert result["persisted"] is False
    assert result["plan_id"] is None


def test_rest_day_in_plan_not_persisted(ctx: AthleteContext) -> None:
    # Plan qui ne couvre pas aujourd'hui → jour hors plan, rien à décider.
    _seed_morning(ctx, _today(), readiness_score=20)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False)
    assert result["persisted"] is False


def test_rest_when_very_short_sleep_and_low_quality(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    # Baseline 6h24 : une nuit à 5h12 n'est pas une « alerte critique » globale,
    # mais c'est très court + qualité mauvaise → repos par la règle qualité.
    _seed_morning(ctx, _today(), baseline_sleep=6.4, sleep_hours=5.2, sleep_score=40, readiness_score=70)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "rest"
    assert "qualité" in result["reason"].lower()


def test_adjust_when_sleep_below_6_5(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    _seed_morning(ctx, _today(), sleep_hours=6.2, readiness_score=80)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "adjust"


def test_adjust_when_sleep_quality_low(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    _seed_morning(ctx, _today(), sleep_hours=6.8, sleep_score=45, readiness_score=80)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "adjust"


def test_adjust_when_sleep_drops_vs_baseline(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    # Baseline 8h30, nuit à 6h54 (-18,8 %) → alerte sommeil → allègement.
    _seed_morning(ctx, _today(), baseline_sleep=8.5, sleep_hours=6.9, readiness_score=80)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "adjust"


def test_go_when_sleep_mildly_short_but_within_normal_variation(ctx: AthleteContext) -> None:
    _plan_for(ctx, _today())
    # Baseline 8h00, nuit à 7h20 (-7,5 %) : pas d'alerte, tout va bien → go.
    _seed_morning(ctx, _today(), baseline_sleep=8.0, sleep_hours=7.3, sleep_score=80, readiness_score=75)
    result = evaluate_daily_decision(_today(), ctx=ctx, use_llm=False, persist=False)
    assert result["decision"] == "go"