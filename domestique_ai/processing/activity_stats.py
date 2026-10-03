"""Agrégats « type de sortie » : mix par sport, kind (zones HR) ou indoor/outdoor.

Alimente le tool coach `get_activity_mix` (extension ``group_by`` +
``include_monthly``). Pure agrégation sur les activités déjà en base — aucune
donnée nouvelle, aucun backfill.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.processing.activity_classify import (
    infer_kind_from_zones,
    sport_bucket,
)
from domestique_ai.processing.analyzer import HR_ZONE_KEYS, fetch_activities_from_db

GroupBy = Literal["sport", "kind", "indoor"]
_GROUP_BY_VALUES = ("sport", "kind", "indoor")
_METRIC_FIELDS = ("sessions", "duration_sec", "distance_km", "elevation_m", "training_load")


def _bucket_key(act: dict[str, Any], group_by: GroupBy) -> str:
    if group_by == "kind":
        zones = {key: act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
        return infer_kind_from_zones(zones, act.get("avg_heart_rate")) or "unknown"
    if group_by == "indoor":
        return sport_bucket(act.get("sport_type"))
    return act.get("sport_type") or "unknown"


def _filter_recent(
    activities: list[dict[str, Any]],
    days: int,
    end: dt.date,
) -> list[dict[str, Any]]:
    """Fenêtre ``[end - days ; end]`` incluse, ancrée sur ``today``.

    Même sémantique que ``llm.tools._filter_recent`` (CR-006) — dupliquée ici
    pour ne pas faire dépendre ``processing`` de la couche LLM.
    """
    if not activities or days <= 0:
        return []
    start = end - dt.timedelta(days=days)
    out = []
    for act in activities:
        raw = act.get("date")
        if not raw:
            continue
        try:
            when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if start <= when <= end:
            out.append(act)
    return out


def _empty() -> dict[str, float]:
    return dict.fromkeys(_METRIC_FIELDS, 0.0)


def _add(bucket: dict[str, float], act: dict[str, Any]) -> None:
    bucket["sessions"] += 1
    bucket["duration_sec"] += act.get("duration") or 0
    bucket["distance_km"] += (act.get("distance") or 0) / 1000
    bucket["elevation_m"] += act.get("elevation_gain") or 0
    bucket["training_load"] += act.get("training_load") or 0


def _row(key: str, values: dict[str, float], key_field: str) -> dict[str, Any]:
    sessions = values["sessions"]
    return {
        key_field: key,
        "sessions": int(sessions),
        "duration_h": round(values["duration_sec"] / 3600, 1),
        "avg_duration_min": round(values["duration_sec"] / sessions / 60, 1) if sessions else 0.0,
        "distance_km": round(values["distance_km"], 1),
        "elevation_m": round(values["elevation_m"], 0),
        "training_load": round(values["training_load"], 1),
    }


def get_activity_mix_stats(
    days: int = 28,
    group_by: str = "sport",
    include_monthly: bool = False,
    *,
    db_path: Path | None = None,
    today: dt.date | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Mix de la pratique par ``sport`` (défaut), ``kind`` (zones HR) ou ``indoor``.

    ``include_monthly`` ajoute l'évolution mensuelle (mois × type) bornée à la
    fenêtre ``days`` (≤ 365 j → ≤ 13 mois).
    """
    if group_by not in _GROUP_BY_VALUES:
        group_by = "sport"
    days = max(1, min(int(days), 365))
    end = today or dt.date.today()
    activities = fetch_activities_from_db(db_path, ctx=ctx)
    recent = _filter_recent(activities, days, end)

    key_field = "sport_type" if group_by == "sport" else "type"
    buckets: dict[str, dict[str, float]] = {}
    monthly: dict[tuple[str, str], dict[str, float]] = {}
    for act in recent:
        key = _bucket_key(act, group_by)
        _add(buckets.setdefault(key, _empty()), act)
        if include_monthly:
            month = (act.get("date") or "")[:7]
            if month:
                _add(monthly.setdefault((month, key), _empty()), act)

    rows = [
        _row(key, values, key_field)
        for key, values in sorted(buckets.items(), key=lambda item: (-item[1]["sessions"], item[0]))
    ]
    result: dict[str, Any] = {
        "as_of": end.isoformat(),
        "days": days,
        "group_by": group_by,
        "total_sessions": len(recent),
    }
    if group_by == "sport":
        result["by_sport"] = rows
        result["sports_count"] = len(rows)
    else:
        result["by_type"] = rows
    if include_monthly:
        result["monthly"] = [
            {"month": month, **_row(key, values, key_field)}
            for (month, key), values in sorted(
                monthly.items(), key=lambda item: (item[0][0], -item[1]["sessions"], item[0][1])
            )
        ]
    return result
