"""Endpoints de listing et détail des activités."""

from __future__ import annotations

import datetime as dt
import time
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query, status

from domestique_ai.api.deps import get_strava_client
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    ActivitiesList,
    ActivityDetail,
    ActivityStreams,
    ActivitySummary,
)
from domestique_ai.ingestion.strava import StravaAuthError, StravaClient
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    fetch_activities_from_db,
)

router = APIRouter(prefix="/api/activities", tags=["activities"])
log = get_logger("activities")

_DETAIL_STREAM_KEYS = [
    "time",
    "latlng",
    "altitude",
    "heartrate",
    "cadence",
    "watts",
    "velocity_smooth",
    "distance",
    "temp",
]
_DETAIL_TTL_SEC = 3600.0
_detail_cache: dict[int, tuple[float, dict]] = {}
_cache_lock = Lock()


def _activity_to_summary(row: dict) -> ActivitySummary:
    distance_m = row.get("distance") or 0
    return ActivitySummary(
        strava_id=int(row["strava_id"]),
        name=row.get("name"),
        date=row.get("date") or "",
        distance_km=round(float(distance_m) / 1000, 2),
        duration_sec=int(row.get("duration") or 0),
        elevation_m=row.get("elevation_gain"),
        avg_hr=row.get("avg_heart_rate"),
        max_hr=row.get("max_heart_rate"),
        avg_power=row.get("avg_power"),
        tss=float(row.get("training_load") or 0.0),
        sport_type=row.get("sport_type"),
        hr_zones_sec={key: row.get(f"hr_{key}_time") for key in HR_ZONE_KEYS},
        avg_temp=row.get("avg_temp"),
        min_temp=row.get("min_temp"),
        max_temp=row.get("max_temp"),
    )


@router.get("", response_model=ActivitiesList)
def list_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    days: int | None = Query(None, ge=1, le=3650),
) -> ActivitiesList:
    """Liste paginée, triée par date décroissante."""
    activities = fetch_activities_from_db()
    if days is not None:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        activities = [
            a
            for a in activities
            if a.get("date")
            and dt.datetime.fromisoformat(a["date"].replace("Z", "+00:00")) >= cutoff
        ]

    activities.sort(key=lambda a: a.get("date") or "", reverse=True)
    total = len(activities)
    start = (page - 1) * page_size
    end = start + page_size
    items = [_activity_to_summary(a) for a in activities[start:end]]
    return ActivitiesList(total=total, page=page, page_size=page_size, items=items)


@router.get("/{strava_id}", response_model=ActivityDetail)
def get_activity(
    strava_id: int,
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
) -> ActivityDetail:
    """Détails complets d'une activité (streams inclus, cache 1 h)."""
    base = next(
        (a for a in fetch_activities_from_db() if a.get("strava_id") == strava_id),
        None,
    )
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {strava_id} introuvable en base.",
        )

    now = time.time()
    with _cache_lock:
        cached = _detail_cache.get(strava_id)
        payload = cached[1] if cached and now - cached[0] < _DETAIL_TTL_SEC else None

    if payload is None:
        log.info("Activité %d : cache miss, fetch Strava…", strava_id)
        try:
            streams = client.fetch_streams_full(strava_id, _DETAIL_STREAM_KEYS) or {}
            summary = client.fetch_activity_summary(strava_id) or {}
        except StravaAuthError as exc:
            log.warning("Activité %d : auth Strava KO : %s", strava_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        payload = {"streams": streams, "summary": summary}
        with _cache_lock:
            _detail_cache[strava_id] = (now, payload)
    else:
        log.debug("Activité %d : cache hit", strava_id)

    summary = payload.get("summary") or {}
    streams = payload.get("streams") or {}

    activity = _activity_to_summary({**base, "name": summary.get("name")})

    hr_zones = {key: base.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
    has_hr_zones = any(v is not None for v in hr_zones.values())

    return ActivityDetail(
        activity=activity,
        streams=ActivityStreams(
            time=streams.get("time"),
            heartrate=streams.get("heartrate"),
            altitude=streams.get("altitude"),
            watts=streams.get("watts"),
            latlng=streams.get("latlng"),
            cadence=streams.get("cadence"),
            velocity_smooth=streams.get("velocity_smooth"),
            distance=streams.get("distance"),
            temp=streams.get("temp"),
        ),
        hr_zones={k: float(v) for k, v in hr_zones.items() if v is not None}
        if has_hr_zones
        else None,
    )
