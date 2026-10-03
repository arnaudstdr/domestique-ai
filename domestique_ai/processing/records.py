"""Records de puissance (best efforts) et découplage Pw:HR.

Calculés **à la volée** depuis les streams persistés (backfill Garmin aligné /
TCX) — aucun stockage dédié. Alimente le tool `get_best_efforts` et le champ
``decoupling_pct`` de `get_activity_details`.

Best efforts : moyenne de puissance maximale sur fenêtres glissantes
(5 s → 60 min), pondérée par le temps réel entre échantillons (pas fixe ~5 s
après compaction). Un effort n'est retenu que si la fenêtre couvre au moins
95 % de la durée demandée.

Découplage (dérive aérobie) : ``(EF1 − EF2) / EF1`` où ``EF = puissance/FC``
par moitié de séance — positif = FC qui dérive à puissance égale. Exige ≥ 20 min
et FC + puissance des deux moitiés.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.morning_metrics import latest_weight, power_to_weight

STANDARD_DURATIONS_SEC = (5, 60, 300, 1200, 3600)
_MIN_WINDOW_RATIO = 0.95
_MAX_SAMPLE_GAP_SEC = 30.0
_PERIOD_DAYS = {"3m": 90, "6m": 182, "1y": 365}


def _resolve_path(db_path: Path | str | None, ctx: AthleteContext | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if ctx is not None:
        return Path(ctx.db_path)
    return Path(get_db_path())


def duration_label(seconds: int) -> str:
    """``300`` → ``"5 min"`` ; ``3600`` → ``"1 h"``."""
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def _points(payload: dict[str, Any]) -> list[tuple[float, float, float | None]]:
    """``(temps, puissance, FC)`` alignés, sans trous de puissance/temps."""
    time = payload.get("time") or []
    power = payload.get("power") or []
    hr = payload.get("heartrate") or []
    count = min(len(time), len(power))
    points: list[tuple[float, float, float | None]] = []
    for i in range(count):
        if time[i] is None or power[i] is None:
            continue
        heart_rate = hr[i] if i < len(hr) and hr[i] is not None else None
        points.append((float(time[i]), float(power[i]), heart_rate))
    return points


def best_effort_watts(
    payload: dict[str, Any],
    duration_sec: float,
) -> float | None:
    """Meilleure moyenne de puissance sur une fenêtre glissante de ``duration_sec``."""
    points = _points(payload)
    if len(points) < 2:
        return None
    times = [point[0] for point in points]

    cumulative_energy = [0.0]
    for i in range(len(points)):
        gap = times[i + 1] - times[i] if i + 1 < len(points) else 0.0
        gap = min(max(gap, 0.0), _MAX_SAMPLE_GAP_SEC)
        cumulative_energy.append(cumulative_energy[-1] + points[i][1] * gap)

    best: float | None = None
    end = 0
    for start in range(len(points)):
        end = max(end, start)
        while end + 1 < len(points) and times[end + 1] - times[start] <= duration_sec:
            end += 1
        span = times[end] - times[start]
        if span < duration_sec * _MIN_WINDOW_RATIO:
            continue
        average = (cumulative_energy[end] - cumulative_energy[start]) / span
        if best is None or average > best:
            best = average
    return round(best, 1) if best is not None else None


def decoupling_pct(payload: dict[str, Any]) -> float | None:
    """Dérive aérobie (``EF1 − EF2) / EF1`` en %, positive si la FC dérive."""
    points = [point for point in _points(payload) if point[2]]
    if len(points) < 2:
        return None
    total = points[-1][0] - points[0][0]
    if total < 1200:
        return None
    midpoint = points[0][0] + total / 2
    first = [point for point in points if point[0] <= midpoint]
    second = [point for point in points if point[0] > midpoint]

    def efficiency_factor(segment: list[tuple[float, float, float | None]]) -> float | None:
        if not segment:
            return None
        power = sum(point[1] for point in segment) / len(segment)
        heart_rate = sum(float(point[2]) for point in segment) / len(segment)  # type: ignore[arg-type]
        return power / heart_rate if heart_rate else None

    first_ef = efficiency_factor(first)
    second_ef = efficiency_factor(second)
    if not first_ef or not second_ef:
        return None
    return round((first_ef - second_ef) / first_ef * 100, 1)


def _period_cutoff(period: str, today: str) -> str | None:
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return None
    date = dt.date.fromisoformat(today)
    return (date - dt.timedelta(days=days)).isoformat()


def _iter_stream_activities(
    conn: sqlite3.Connection, cutoff: str | None
) -> list[tuple[int, str, str | None, dict[str, Any]]]:
    rows = conn.execute(
        "SELECT a.id, a.date, a.name, s.payload FROM activities a "
        "JOIN activity_streams s ON s.activity_id = a.id ORDER BY a.date ASC"
    ).fetchall()
    activities = []
    for activity_id, date, name, payload_json in rows:
        if cutoff and (date or "")[:10] < cutoff:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        activities.append((int(activity_id), date, name, payload))
    return activities


def records_report(
    period: str = "1y",
    duration_min: int | None = None,
    *,
    limit: int = 5,
    today: dt.date | None = None,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Records de puissance : par durée standard, ou top efforts d'une durée ciblée."""
    path = _resolve_path(db_path, ctx)
    if not Path(path).exists():
        return {"available": False, "reason": "Base athlète introuvable."}
    if period not in {"3m", "6m", "1y", "all"}:
        period = "1y"
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        cutoff = _period_cutoff(period, (today or dt.date.today()).isoformat())
        activities = _iter_stream_activities(conn, cutoff)
    finally:
        conn.close()

    if not activities:
        return {
            "available": False,
            "reason": "Aucun stream persisté sur la période — backfill Garmin requis "
            "(python -m domestique_ai.ingestion.backfill_streams).",
        }

    weight = latest_weight(db_path=path)

    def wkg(watts: float | None) -> float | None:
        return power_to_weight(watts, weight)

    if duration_min is not None:
        duration_sec = max(1, min(int(duration_min), 180)) * 60
        efforts = []
        for activity_id, date, name, payload in activities:
            watts = best_effort_watts(payload, duration_sec)
            if watts is not None:
                efforts.append(
                    {
                        "watts": watts,
                        "wkg": wkg(watts),
                        "date": date,
                        "activity": name,
                        "activity_id": activity_id,
                    }
                )
        efforts.sort(key=lambda effort: effort["watts"], reverse=True)
        if not efforts:
            return {
                "available": False,
                "reason": f"Aucun effort de {duration_label(duration_sec)} calculable "
                "(puissance absente des streams ou séances trop courtes).",
            }
        by_year: dict[str, dict[str, Any]] = {}
        for effort in efforts:
            year = (effort["date"] or "")[:4]
            if year and (year not in by_year or effort["watts"] > by_year[year]["watts"]):
                by_year[year] = effort
        return {
            "available": True,
            "period": period,
            "duration_sec": duration_sec,
            "duration_label": duration_label(duration_sec),
            "best": efforts[0],
            "top": efforts[: max(1, min(int(limit), 10))],
            "by_year": [by_year[year] for year in sorted(by_year)],
        }

    records = []
    for duration_sec in STANDARD_DURATIONS_SEC:
        best: dict[str, Any] | None = None
        for activity_id, date, name, payload in activities:
            watts = best_effort_watts(payload, duration_sec)
            if watts is None:
                continue
            if best is None or watts > best["watts"]:
                best = {
                    "watts": watts,
                    "wkg": wkg(watts),
                    "date": date,
                    "activity": name,
                    "activity_id": activity_id,
                }
        if best is not None:
            records.append(
                {
                    "duration_sec": duration_sec,
                    "duration_label": duration_label(duration_sec),
                    **best,
                }
            )
    if not records:
        return {
            "available": False,
            "reason": "Aucun effort calculable (puissance absente des streams persistés).",
        }

    # Tendance seuil : meilleur 20 min par année (répond à « j'ai progressé au seuil ? »).
    threshold_by_year: dict[str, dict[str, Any]] = {}
    for _activity_id, date, name, payload in activities:
        watts = best_effort_watts(payload, 1200)
        if watts is None:
            continue
        year = (date or "")[:4]
        if year and (year not in threshold_by_year or watts > threshold_by_year[year]["watts"]):
            threshold_by_year[year] = {
                "year": year,
                "watts": watts,
                "wkg": wkg(watts),
                "date": date,
                "activity": name,
            }
    return {
        "available": True,
        "period": period,
        "records": records,
        "threshold_by_year": [threshold_by_year[year] for year in sorted(threshold_by_year)],
        "activities_with_streams": len(activities),
    }
