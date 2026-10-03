"""Tests records de puissance + découplage Pw:HR (processing.records)."""

from __future__ import annotations

import datetime as dt
import sqlite3

from domestique_ai.ingestion.db import init_db, store_activity_streams
from domestique_ai.llm.tools import dispatch
from domestique_ai.processing.records import (
    best_effort_watts,
    decoupling_pct,
    records_report,
)

TODAY = dt.date(2026, 4, 30)
STEP_SEC = 5.0


def _steady_payload(watts: float, minutes: int, hr: float | None = None) -> dict:
    count = int(minutes * 60 / STEP_SEC)
    return {
        "time": [i * STEP_SEC for i in range(count)],
        "power": [watts] * count,
        "heartrate": ([hr] * count) if hr else [],
        "distance": [i * 25.0 for i in range(count)],
        "altitude": [300.0] * count,
    }


def _seed(db_path, activity_id: int, strava_id: int, date: str, payload: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activities (id, strava_id, date, duration, sport_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (activity_id, strava_id, date, 2400, "Ride"),
        )
        conn.commit()
    finally:
        conn.close()
    store_activity_streams(activity_id, payload, db_path=db_path)


def test_best_effort_constant_power():
    payload = _steady_payload(200.0, 40)
    assert best_effort_watts(payload, 5) == 200.0
    assert best_effort_watts(payload, 1200) == 200.0
    assert best_effort_watts(payload, 3600) is None  # séance de 40 min seulement


def test_best_effort_picks_best_window():
    count = int(12 * 60 / STEP_SEC)
    time = [i * STEP_SEC for i in range(count)]
    power = [400.0 if t < 300 else 200.0 for t in time]
    payload = {"time": time, "power": power}
    assert best_effort_watts(payload, 300) == 400.0
    assert best_effort_watts(payload, 600) == 300.0  # 5 min à 400 + 5 min à 200


def test_decoupling_positive_when_hr_drifts():
    count = int(40 * 60 / STEP_SEC)
    half = count // 2
    payload = {
        "time": [i * STEP_SEC for i in range(count)],
        "power": [200.0] * count,
        "heartrate": [140.0] * half + [150.0] * (count - half),
    }
    pct = decoupling_pct(payload)
    assert pct is not None
    assert 6.0 <= pct <= 7.0


def test_decoupling_none_without_hr_or_short():
    assert decoupling_pct(_steady_payload(200.0, 40)) is None
    assert decoupling_pct(_steady_payload(200.0, 10, hr=140.0)) is None


def test_records_report_best_and_period(tmp_path):
    db = tmp_path / "records.db"
    init_db(db)
    _seed(db, 1, 1, "2026-04-05T08:00:00Z", _steady_payload(200.0, 40))
    _seed(db, 2, 2, "2026-04-12T08:00:00Z", _steady_payload(260.0, 40))

    report = records_report(period="1y", today=TODAY, db_path=db)
    assert report["available"] is True
    record_5s = next(row for row in report["records"] if row["duration_sec"] == 5)
    assert record_5s["watts"] == 260.0
    assert record_5s["date"].startswith("2026-04-12")
    assert len(report["threshold_by_year"]) == 1
    assert report["threshold_by_year"][0]["watts"] == 260.0

    # Fenêtre 3 mois d'une date ultérieure : activités hors période.
    excluded = records_report(period="3m", today=dt.date(2027, 1, 15), db_path=db)
    assert excluded["available"] is False


def test_records_report_focused_duration(tmp_path):
    db = tmp_path / "records.db"
    init_db(db)
    _seed(db, 1, 1, "2026-04-05T08:00:00Z", _steady_payload(200.0, 40))
    _seed(db, 2, 2, "2026-04-12T08:00:00Z", _steady_payload(260.0, 40))

    focused = records_report(period="1y", duration_min=20, today=TODAY, db_path=db)
    assert focused["available"] is True
    assert focused["duration_label"] == "20 min"
    assert focused["best"]["watts"] == 260.0
    assert focused["best"]["date"].startswith("2026-04-12")
    assert len(focused["top"]) == 2


def test_get_best_efforts_tool_dispatch(tmp_path, monkeypatch):
    db = tmp_path / "records.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    init_db(db)
    _seed(db, 1, 1, "2026-04-05T08:00:00Z", _steady_payload(200.0, 40))

    out = dispatch("get_best_efforts", {"duration_min": 20})
    assert out["available"] is True
    assert out["best"]["watts"] == 200.0
