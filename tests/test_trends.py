"""Tests unitaires pour les agrégats de tendances longue durée."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from domestique_ai.ingestion.strava import init_db
from domestique_ai.processing.trends import (
    _bucket_load_curve,
    _months_in_range,
    _shift_month_one_year,
    get_ftp_projection,
    get_trends,
)


def _seed_activities(db_path, rows: list[dict]) -> None:
    """Insère des activités minimales dans une DB tmp (training_load déjà calculé)."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for row in rows:
            conn.execute(
                "INSERT INTO activities (strava_id, date, duration, "
                "avg_heart_rate, avg_power, elevation_gain, distance, "
                "training_load, sport_type, "
                "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["strava_id"],
                    row["date"],
                    row.get("duration", 3600),
                    row.get("avg_heart_rate"),
                    row.get("avg_power"),
                    row.get("elevation_gain", 0),
                    row.get("distance", 0),
                    row.get("training_load", 0.0),
                    row.get("sport_type", "Ride"),
                    row.get("hr_z1_time"),
                    row.get("hr_z2_time"),
                    row.get("hr_z3_time"),
                    row.get("hr_z4_time"),
                    row.get("hr_z5_time"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------- Helpers internes -------------------------------------------------


def test_months_in_range_handles_year_boundary():
    months = _months_in_range(dt.date(2025, 11, 15), dt.date(2026, 2, 3))
    assert months == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_months_in_range_single_month():
    months = _months_in_range(dt.date(2026, 5, 1), dt.date(2026, 5, 28))
    assert months == ["2026-05"]


def test_shift_month_one_year():
    assert _shift_month_one_year("2026-05") == "2025-05"
    assert _shift_month_one_year("2024-01") == "2023-01"


def test_bucket_load_curve_keeps_last_value_per_bucket():
    curves = [
        {"date": "2026-05-04", "CTL": 50.0, "ATL": 60.0, "TSB": -10.0},
        {"date": "2026-05-05", "CTL": 51.0, "ATL": 61.0, "TSB": -10.0},
        {"date": "2026-05-10", "CTL": 53.0, "ATL": 55.0, "TSB": -2.0},  # même semaine ISO
        {"date": "2026-05-12", "CTL": 54.0, "ATL": 58.0, "TSB": -4.0},  # semaine suivante
    ]
    bucketed = _bucket_load_curve(curves, "week")
    # On garde la dernière valeur de chaque semaine (W19 puis W20).
    assert len(bucketed) == 2
    assert bucketed[0]["date"] == "2026-05-10"  # dernière de la W19
    assert bucketed[1]["date"] == "2026-05-12"  # seule de la W20


def test_bucket_load_curve_day_resolution_is_identity():
    curves = [
        {"date": "2026-05-04", "CTL": 50.0, "ATL": 60.0, "TSB": -10.0},
        {"date": "2026-05-05", "CTL": 51.0, "ATL": 61.0, "TSB": -10.0},
    ]
    assert _bucket_load_curve(curves, "day") == curves


# ---------- get_trends() -----------------------------------------------------


def test_get_trends_returns_empty_payload_when_no_db(tmp_path):
    db = tmp_path / "missing.db"
    result = get_trends("3m", db_path=db, today=dt.date(2026, 5, 21))
    assert result["period"] == "3m"
    assert result["resolution"] == "day"
    assert result["load_history"] == []
    # Aucune activité → aucun mois agrégé.
    assert result["monthly"] == []


def test_get_trends_aggregates_monthly_volumes(tmp_path):
    db = tmp_path / "trends.db"
    _seed_activities(db, [
        {
            "strava_id": 1, "date": "2026-05-10T08:00:00Z",
            "distance": 50_000, "elevation_gain": 600,
            "duration": 7200, "training_load": 80.0,
        },
        {
            "strava_id": 2, "date": "2026-05-15T08:00:00Z",
            "distance": 30_000, "elevation_gain": 200,
            "duration": 3600, "training_load": 50.0,
        },
        {
            "strava_id": 3, "date": "2026-04-20T08:00:00Z",
            "distance": 40_000, "elevation_gain": 400,
            "duration": 5400, "training_load": 60.0,
        },
    ])

    result = get_trends("3m", db_path=db, today=dt.date(2026, 5, 21))
    by_month = {entry["month"]: entry for entry in result["monthly"]}

    assert by_month["2026-05"]["distance_km"] == pytest.approx(80.0)
    assert by_month["2026-05"]["elevation_m"] == 800
    assert by_month["2026-05"]["duration_sec"] == 10_800
    assert by_month["2026-05"]["sessions"] == 2
    assert by_month["2026-05"]["tss"] == pytest.approx(130.0)

    assert by_month["2026-04"]["distance_km"] == pytest.approx(40.0)
    assert by_month["2026-04"]["sessions"] == 1


def test_get_trends_n1_comparison_when_available(tmp_path):
    db = tmp_path / "trends_n1.db"
    _seed_activities(db, [
        # Année courante : mai 2026
        {
            "strava_id": 1, "date": "2026-05-10T08:00:00Z",
            "distance": 50_000, "training_load": 80.0,
        },
        # Année N-1 : mai 2025 — doit apparaître en distance_km_n1.
        {
            "strava_id": 100, "date": "2025-05-10T08:00:00Z",
            "distance": 30_000, "training_load": 50.0,
        },
    ])

    result = get_trends("all", db_path=db, today=dt.date(2026, 5, 21))
    by_month = {entry["month"]: entry for entry in result["monthly"]}
    assert by_month["2026-05"]["distance_km"] == pytest.approx(50.0)
    assert by_month["2026-05"]["distance_km_n1"] == pytest.approx(30.0)
    assert by_month["2026-05"]["tss_n1"] == pytest.approx(50.0)


def test_get_trends_zones_distribution_pct(tmp_path):
    db = tmp_path / "trends_zones.db"
    _seed_activities(db, [
        {
            "strava_id": 1, "date": "2026-05-10T08:00:00Z",
            "training_load": 100.0,
            "hr_z1_time": 0.0, "hr_z2_time": 3000.0,
            "hr_z3_time": 600.0, "hr_z4_time": 0.0, "hr_z5_time": 0.0,
        },
        {
            "strava_id": 2, "date": "2026-05-15T08:00:00Z",
            "training_load": 50.0,
            "hr_z1_time": 0.0, "hr_z2_time": 0.0,
            "hr_z3_time": 0.0, "hr_z4_time": 300.0, "hr_z5_time": 100.0,
        },
    ])

    result = get_trends("3m", db_path=db, today=dt.date(2026, 5, 21))
    may = next(e for e in result["monthly"] if e["month"] == "2026-05")
    # Total: 4000 s — 0/3000/600/300/100 → 0/75/15/7.5/2.5 %
    assert may["z2_pct"] == pytest.approx(75.0, abs=0.1)
    assert may["z3_pct"] == pytest.approx(15.0, abs=0.1)
    assert may["z4_pct"] == pytest.approx(7.5, abs=0.1)
    assert may["z5_pct"] == pytest.approx(2.5, abs=0.1)


def test_get_trends_zones_none_when_not_ventilated(tmp_path):
    db = tmp_path / "trends_no_zones.db"
    _seed_activities(db, [
        {
            "strava_id": 1, "date": "2026-05-10T08:00:00Z",
            "training_load": 80.0,
            # Toutes les zones HR à None : activité non ventilée.
        },
    ])

    result = get_trends("3m", db_path=db, today=dt.date(2026, 5, 21))
    may = next(e for e in result["monthly"] if e["month"] == "2026-05")
    assert may["z1_pct"] is None
    assert may["z5_pct"] is None


def test_get_trends_resolution_per_period(tmp_path):
    db = tmp_path / "trends_res.db"
    # Une activité ancienne pour qu'il y ait de l'historique CTL.
    _seed_activities(db, [
        {
            "strava_id": 1, "date": "2025-05-15T08:00:00Z",
            "training_load": 80.0,
        },
        {
            "strava_id": 2, "date": "2026-05-15T08:00:00Z",
            "training_load": 80.0,
        },
    ])

    today = dt.date(2026, 5, 21)
    assert get_trends("3m", db_path=db, today=today)["resolution"] == "day"
    assert get_trends("6m", db_path=db, today=today)["resolution"] == "week"
    assert get_trends("1y", db_path=db, today=today)["resolution"] == "week"
    assert get_trends("all", db_path=db, today=today)["resolution"] == "month"


def test_get_trends_rejects_unknown_period(tmp_path):
    db = tmp_path / "trends_bad.db"
    with pytest.raises(ValueError, match="period inconnue"):
        get_trends("42d", db_path=db, today=dt.date(2026, 5, 21))  # type: ignore[arg-type]


# ---------- get_ftp_projection() ---------------------------------------------


def test_ftp_projection_no_activities_yields_low_confidence(tmp_path, monkeypatch):
    """Pas d'historique → confiance ``low``, delta nul, projection = FTP courante.

    ``get_ftp()`` retombe sur la valeur par défaut 250.0 W (fallback partagé avec
    le calcul TSS), donc ``current_ftp`` n'est jamais ``None`` ici — c'est OK,
    l'absence de delta_ctl_28d suffit à signaler qu'on ne peut rien projeter de
    significatif (delta_pct = 0).
    """
    monkeypatch.delenv("STRAVA_FTP", raising=False)
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "no_profile.yaml"))
    db = tmp_path / "no_acts.db"

    result = get_ftp_projection(db_path=db, today=dt.date(2026, 5, 21))
    assert result["confidence"] == "low"
    assert result["history_days"] == 0
    assert result["delta_pct"] == pytest.approx(0.0)
    assert result["delta_ctl_28d"] is None
    assert result["projected_ftp"] is None  # pas de delta → pas de projection


def test_ftp_projection_positive_ctl_progression_yields_positive_delta(
    tmp_path, monkeypatch
):
    """CTL en hausse sur 28 j → ``delta_pct`` positif, plafonné à +5 %."""
    monkeypatch.setenv("STRAVA_FTP", "250")
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "no_profile.yaml"))

    db = tmp_path / "ftp_pos.db"
    # 60 jours de charge progressive : CTL monte clairement.
    rows = []
    base_date = dt.date(2026, 3, 23)  # ~60 jours avant 2026-05-21
    for i in range(60):
        rows.append({
            "strava_id": 1000 + i,
            "date": f"{(base_date + dt.timedelta(days=i)).isoformat()}T08:00:00Z",
            # Plus chargé sur la fin de période → CTL accélère.
            "training_load": 50.0 if i < 30 else 100.0,
        })
    _seed_activities(db, rows)

    result = get_ftp_projection(db_path=db, today=dt.date(2026, 5, 21))
    assert result["current_ftp"] == pytest.approx(250.0)
    assert result["delta_pct"] > 0
    assert result["delta_pct"] <= 5.0  # plafonnement strict.
    assert result["projected_ftp"] is not None
    assert result["projected_ftp"] > 250.0
    # Confiance au moins medium : on a 60 jours d'historique.
    assert result["confidence"] in {"medium", "high"}


def test_ftp_projection_delta_pct_clamped_to_minus_5(tmp_path, monkeypatch):
    """CTL en chute libre → ``delta_pct`` plancher à -5 %."""
    monkeypatch.setenv("STRAVA_FTP", "300")
    monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", str(tmp_path / "no_profile.yaml"))

    db = tmp_path / "ftp_neg.db"
    # Forte charge il y a > 28 jours puis silence radio → CTL chute beaucoup
    # (≥ -25, soit gain_pct = -5).
    rows = []
    for i in range(30):
        rows.append({
            "strava_id": 2000 + i,
            "date": f"{(dt.date(2026, 3, 1) + dt.timedelta(days=i)).isoformat()}T08:00:00Z",
            "training_load": 200.0,
        })
    _seed_activities(db, rows)

    result = get_ftp_projection(db_path=db, today=dt.date(2026, 5, 21))
    assert result["delta_pct"] == pytest.approx(-5.0)
    assert result["projected_ftp"] == pytest.approx(round(300.0 * 0.95, 1))
