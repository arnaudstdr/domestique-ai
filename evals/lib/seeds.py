"""Données synthétiques des scénarios d'évaluation.

Aucune donnée réelle : tout est construit à la main sur une date figée
(2026-04-30, convention des tests du dépôt) pour que les valeurs des tools
soient reproductibles d'un run à l'autre. Les scénarios couvrent les cas
sensibles : athlète sans données, semaine normale, surentraînement, reprise
après coupure, semaine à disponibilité réduite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.morning_metrics import save_morning_entry

SCENARIOS = {"empty", "normal", "overtraining", "comeback", "busy_week"}


@dataclass(frozen=True)
class SeedPaths:
    """Chemins des artefacts seedés (base + YAML profil/objectif/dispo)."""

    db_path: Path
    profile_path: Path
    objective_path: Path
    availability_path: Path


_PROFILE_YAML = """\
ftp: 250
hr_rest: 50
hr_max: 190
sex: M
lthr_pct: 0.88
level: intermediate
"""

_OBJECTIVE_YAML = """\
type: cyclosportive
date: 2026-06-28
distance_km: 120
elevation_m: 1500
notes: Objectif synthétique du harnais d'évaluation.
"""

_AVAILABILITY_YAML = """\
days:
  wednesday:
    max_duration_min: 90
    context: indoor
  thursday:
    max_duration_min: 90
    context: indoor
  saturday:
    max_duration_min: 240
    context: outdoor
  sunday:
    max_duration_min: 240
    context: outdoor
"""

_AVAILABILITY_LIMITED_YAML = """\
days:
  wednesday:
    max_duration_min: 60
    context: indoor
  saturday:
    max_duration_min: 120
    context: outdoor
"""

# (strava_id, date, duration_s, avg_hr, max_hr, avg_power, elevation_m, distance_m,
#  training_load, hr_z1..hr_z5 en secondes)
_NORMAL_ROWS: list[tuple[object, ...]] = [
    (101, "2026-03-23T08:00:00Z", 3600, 140, 168, 180, 300, 30000, 60, 1200, 1800, 600, 0, 0),
    (102, "2026-03-25T17:00:00Z", 2700, 150, 175, 220, 150, 20000, 70, 600, 900, 900, 300, 0),
    (103, "2026-03-28T09:00:00Z", 5400, 138, 165, 175, 500, 60000, 95, 1800, 2700, 900, 0, 0),
    (104, "2026-04-01T17:00:00Z", 3000, 152, 178, 230, 120, 22000, 75, 600, 900, 1200, 300, 0),
    (105, "2026-04-04T09:00:00Z", 7200, 140, 170, 190, 700, 80000, 120, 2400, 3600, 1200, 0, 0),
    (106, "2026-04-08T17:00:00Z", 3600, 148, 172, 210, 200, 28000, 80, 900, 1500, 900, 300, 0),
    (107, "2026-04-11T09:00:00Z", 9000, 142, 168, 195, 900, 100000, 150, 3000, 4200, 1800, 0, 0),
    (108, "2026-04-15T17:00:00Z", 2700, 155, 180, 240, 100, 18000, 70, 300, 600, 1200, 600, 0),
    (109, "2026-04-18T09:00:00Z", 6300, 140, 166, 185, 600, 70000, 110, 2400, 2700, 1200, 0, 0),
    (110, "2026-04-22T17:00:00Z", 3600, 150, 174, 215, 180, 26000, 85, 900, 1200, 1200, 300, 0),
    (111, "2026-04-25T09:00:00Z", 8100, 143, 170, 190, 800, 90000, 135, 3000, 3600, 1500, 0, 0),
    (112, "2026-04-27T17:00:00Z", 3000, 146, 171, 205, 150, 22000, 65, 600, 1200, 900, 300, 0),
    (113, "2026-04-29T17:00:00Z", 2400, 135, 160, 170, 80, 15000, 40, 900, 900, 600, 0, 0),
]

# Semaine de surcharge : séances quotidiennes à forte charge, TSS 140-185.
_OVERLOAD_ROWS: list[tuple[object, ...]] = [
    (114, "2026-04-20T17:00:00Z", 7200, 158, 184, 260, 500, 65000, 165, 600, 1200, 2400, 2400, 600),
    (115, "2026-04-21T17:00:00Z", 5400, 160, 186, 255, 400, 50000, 150, 300, 900, 1800, 1800, 600),
    (116, "2026-04-23T17:00:00Z", 6300, 159, 185, 258, 450, 56000, 160, 300, 900, 2100, 2100, 900),
    (117, "2026-04-24T17:00:00Z", 5400, 162, 188, 262, 380, 48000, 155, 300, 600, 1500, 2400, 600),
    (
        118,
        "2026-04-26T09:00:00Z",
        9000,
        155,
        182,
        250,
        900,
        100000,
        185,
        1200,
        2100,
        2700,
        2400,
        600,
    ),
    (119, "2026-04-28T17:00:00Z", 6300, 161, 187, 256, 420, 54000, 158, 300, 600, 1800, 2700, 900),
    (120, "2026-04-30T17:00:00Z", 5400, 163, 189, 260, 350, 46000, 152, 0, 600, 1800, 2400, 600),
]

# Reprise : 3 semaines d'activités en mars, rien depuis (coupure > 1 mois).
_COMEBACK_ROWS: list[tuple[object, ...]] = [
    (201, "2026-03-02T17:00:00Z", 3600, 145, 172, 200, 200, 26000, 70, 900, 1500, 900, 300, 0),
    (202, "2026-03-06T09:00:00Z", 5400, 140, 168, 185, 500, 60000, 95, 1800, 2400, 1200, 0, 0),
    (203, "2026-03-11T17:00:00Z", 3000, 148, 176, 215, 120, 20000, 65, 600, 900, 900, 600, 0),
    (204, "2026-03-15T09:00:00Z", 7200, 138, 164, 175, 650, 78000, 115, 2700, 3300, 1200, 0, 0),
    (205, "2026-03-20T17:00:00Z", 2700, 152, 178, 225, 90, 17000, 60, 300, 600, 1200, 600, 0),
]


def seed_scenario(root: Path, scenario: str) -> SeedPaths:
    """Crée une base athlète synthétique + les YAML du scénario demandé."""
    if scenario not in SCENARIOS:
        raise ValueError(f"scénario inconnu : {scenario!r} (attendu : {sorted(SCENARIOS)})")
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "athlete.db"
    profile_path = root / "profile.yaml"
    objective_path = root / "objective.yaml"
    availability_path = root / "availability.yaml"
    init_db(db_path)
    if scenario == "empty":
        return SeedPaths(db_path, profile_path, objective_path, availability_path)

    profile_path.write_text(_PROFILE_YAML, encoding="utf-8")
    objective_path.write_text(_OBJECTIVE_YAML, encoding="utf-8")
    availability = _AVAILABILITY_LIMITED_YAML if scenario == "busy_week" else _AVAILABILITY_YAML
    availability_path.write_text(availability, encoding="utf-8")

    rows = _COMEBACK_ROWS if scenario == "comeback" else _NORMAL_ROWS
    conn = sqlite3.connect(db_path)
    try:
        _insert_activities(conn, rows)
        if scenario == "overtraining":
            _insert_activities(conn, _OVERLOAD_ROWS)
    finally:
        conn.close()
    if scenario == "overtraining":
        _seed_fatigue_morning(db_path)
    return SeedPaths(db_path, profile_path, objective_path, availability_path)


def _insert_activities(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> None:
    conn.executemany(
        "INSERT INTO activities ("
        "strava_id, date, duration, avg_heart_rate, max_heart_rate, "
        "avg_power, elevation_gain, distance, training_load, "
        "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def _seed_fatigue_morning(db_path: Path) -> None:
    """Marqueurs de fatigue : HRV en baisse, FC repos en hausse, sommeil dégradé."""
    entries = [
        ("2026-04-26", 52.0, 50.0, 6.8),
        ("2026-04-27", 48.0, 52.0, 6.4),
        ("2026-04-28", 43.0, 54.0, 6.0),
        ("2026-04-29", 38.0, 56.0, 5.6),
        ("2026-04-30", 34.0, 58.0, 5.2),
    ]
    for day, hrv, resting_hr, sleep_hours in entries:
        save_morning_entry(
            day,
            hrv_ms=hrv,
            resting_hr=resting_hr,
            sleep_hours=sleep_hours,
            db_path=db_path,
        )
