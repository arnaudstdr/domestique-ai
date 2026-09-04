"""
Comparaison plan vs réalisé (compliance).

Rapproche les séances planifiées (``Workout`` d'un plan) avec les activités
réellement ingérées (table ``activities``) sur une fenêtre hebdomadaire, en
tenant compte des décisions du check du matin (``plan_decisions``) : un repos
décidé par le coach n'est PAS une séance manquée.

C'est l'entrée de la revue hebdomadaire (``llm/weekly_review``) et le rapport
affiché en UI (fait / manqué / delta TSS).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from domestique_ai.processing.plan_builder import Workout
from domestique_ai.processing.similar import _sport_bucket

# Fraction de la durée planifiée en dessous de laquelle on considère la séance
# comme « partielle » plutôt que « faite ».
_PARTIAL_THRESHOLD = 0.5

_CYCLING_BUCKETS = {"indoor", "outdoor"}


def week_boundaries(anchor: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Bornes lundi-dimanche de la semaine contenant ``anchor`` (défaut: aujourd'hui)."""
    anchor = anchor or _dt.date.today()
    start = anchor - _dt.timedelta(days=anchor.weekday())
    return start, start + _dt.timedelta(days=6)


def _planned_bucket(workout: Workout) -> str | None:
    """Bucket indoor/outdoor déduit des notes du plan, sinon None (indifférent)."""
    notes = (workout.notes or "").lower()
    if "indoor" in notes:
        return "indoor"
    if "outdoor" in notes:
        return "outdoor"
    return None


def _match_activity(workout: Workout, day_activities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Sélectionne l'activité du jour qui correspond à la séance planifiée.

    Filtre sur le bucket sport (indoor/outdoor cyclisme) quand il est déductible
    des notes, puis retient l'activité à la charge la plus élevée.
    """
    bucket = _planned_bucket(workout)
    if bucket is None:
        candidates = [a for a in day_activities if _sport_bucket(a.get("sport_type")) in _CYCLING_BUCKETS]
    else:
        candidates = [a for a in day_activities if _sport_bucket(a.get("sport_type")) == bucket]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.get("training_load") or 0.0)


def compute_week_compliance(
    plan: list[Workout],
    activities: list[dict[str, Any]],
    *,
    week_start: _dt.date | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rapport de compliance d'une semaine.

    ``plan`` : séances du plan (Workout). ``activities`` : activités ingérées
    (rows ``fetch_activities_from_db``). ``decisions`` : décisions du check du
    matin (rows ``list_decisions``). ``week_start`` : lundi de la semaine à
    auditer (défaut: semaine courante).

    Retourne un dict avec les agrégats (fait/partiel/manqué/skippé, adhérence,
    TSS planifié vs réalisé) et le détail ``per_day``.
    """
    start, end = week_boundaries(week_start)
    decisions_by_date: dict[str, dict[str, Any]] = {
        d["date"]: d for d in (decisions or [])
    }
    planned_by_date: dict[str, Workout] = {
        w.date: w for w in plan if start.isoformat() <= w.date <= end.isoformat()
    }
    activities_by_date: dict[str, list[dict[str, Any]]] = {}
    for a in activities:
        date_str = str(a.get("date") or "")[:10]
        if start.isoformat() <= date_str <= end.isoformat():
            activities_by_date.setdefault(date_str, []).append(a)

    per_day: list[dict[str, Any]] = []
    done = partial = missed = skipped = 0
    planned_tss = 0.0
    realized_tss = 0.0

    for offset in range(7):
        day = start + _dt.timedelta(days=offset)
        date_str = day.isoformat()
        workout = planned_by_date.get(date_str)
        decision = decisions_by_date.get(date_str)
        day_activities = activities_by_date.get(date_str, [])

        entry: dict[str, Any] = {
            "date": date_str,
            "weekday": day.weekday(),
            "planned": None,
            "decision": None,
            "status": "off",
            "realized": None,
        }
        if workout is not None:
            entry["planned"] = {
                "uid": workout.uid,
                "name": workout.name,
                "kind": workout.kind,
                "duration_min": workout.duration_min,
                "estimated_tss": workout.estimated_tss,
            }
            planned_tss += workout.estimated_tss or 0.0
        if decision is not None:
            entry["decision"] = {
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
                "decided_by": decision.get("decided_by"),
            }

        if workout is None:
            per_day.append(entry)
            continue

        if decision is not None and decision.get("decision") == "rest":
            entry["status"] = "rest"
            skipped += 1
            per_day.append(entry)
            continue

        match = _match_activity(workout, day_activities)
        if match is not None:
            duration_sec = match.get("duration") or 0
            planned_sec = (workout.duration_min or 0) * 60
            status = "done" if duration_sec >= _PARTIAL_THRESHOLD * planned_sec else "partial"
            entry["status"] = status
            entry["realized"] = {
                "external_id": match.get("garmin_id") or match.get("strava_id"),
                "duration_sec": duration_sec,
                "training_load": match.get("training_load"),
                "sport_type": match.get("sport_type"),
                "distance_km": (match.get("distance") or 0.0) / 1000.0,
            }
            realized_tss += match.get("training_load") or 0.0
            if status == "done":
                done += 1
            else:
                partial += 1
        else:
            entry["status"] = "missed"
            missed += 1
        per_day.append(entry)

    expected = planned_by_date and len(planned_by_date) - skipped
    adherence_pct = round((done / expected) * 100, 1) if expected and expected > 0 else 0.0
    tss_delta_pct = (
        round(((realized_tss - planned_tss) / planned_tss) * 100, 1)
        if planned_tss > 0
        else None
    )

    return {
        "available": bool(plan),
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "planned_sessions": len(planned_by_date),
        "done": done,
        "partial": partial,
        "missed": missed,
        "skipped_by_decision": skipped,
        "adherence_pct": adherence_pct,
        "planned_tss": round(planned_tss, 1),
        "realized_tss": round(realized_tss, 1),
        "tss_delta_pct": tss_delta_pct,
        "per_day": per_day,
    }