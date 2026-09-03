"""Endpoints de listing et détail des activités."""

from __future__ import annotations

import datetime as dt
import time
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query, status

from domestique_ai.api.deps import get_athlete_context, get_strava_client
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    ActivitiesList,
    ActivityDetail,
    ActivityStreams,
    ActivitySummary,
    SimilarActivitiesResponse,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.strava import StravaAuthError, StravaClient
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    fetch_activities_from_db,
)
from domestique_ai.processing.similar import find_similar_activities

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
# Clé préfixée par l'espace de données (db_path) pour isoler le cache par athlète.
_detail_cache: dict[tuple[str, int], tuple[float, dict]] = {}
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
        map_polyline=row.get("map_polyline"),
    )


def _parse_iso_utc(value: str) -> dt.datetime:
    """Parse un ISO date (``YYYY-MM-DD`` ou datetime complet) en UTC."""
    when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when


def _activity_date(a: dict) -> dt.datetime | None:
    raw = a.get("date")
    if not raw:
        return None
    try:
        return _parse_iso_utc(raw)
    except ValueError:
        return None


@router.get("", response_model=ActivitiesList)
def list_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    days: int | None = Query(None, ge=1, le=3650),
    date_from: str | None = Query(None, description="Date min (YYYY-MM-DD), inclusive"),
    date_to: str | None = Query(None, description="Date max (YYYY-MM-DD), inclusive"),
    sport_types: list[str] | None = Query(None, description="Répétable"),  # noqa: B008
    distance_min_km: float | None = Query(None, ge=0),
    distance_max_km: float | None = Query(None, ge=0),
    elevation_min_m: float | None = Query(None, ge=0),
    elevation_max_m: float | None = Query(None, ge=0),
    duration_min_sec: int | None = Query(None, ge=0),
    duration_max_sec: int | None = Query(None, ge=0),
    tss_min: float | None = Query(None, ge=0),
    tss_max: float | None = Query(None, ge=0),
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivitiesList:
    """Liste paginée, triée par date décroissante, avec filtres optionnels.

    Tous les paramètres de filtre sont combinés en ET logique. Les bornes
    numériques sont inclusives (``>=`` / ``<=``). ``date_to`` est inclusive
    au jour près : ``date_to=2025-04-30`` accepte les activités du 30 avril
    jusqu'à 23h59:59 UTC.
    """
    activities = fetch_activities_from_db(ctx=ctx)
    if days is not None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        activities = [
            a for a in activities if (when := _activity_date(a)) is not None and when >= cutoff
        ]

    if date_from:
        try:
            from_dt = _parse_iso_utc(date_from)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"date_from invalide : {date_from!r}",
            ) from exc
        activities = [
            a for a in activities if (when := _activity_date(a)) is not None and when >= from_dt
        ]

    if date_to:
        try:
            to_dt = _parse_iso_utc(date_to) + dt.timedelta(days=1)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"date_to invalide : {date_to!r}",
            ) from exc
        activities = [
            a for a in activities if (when := _activity_date(a)) is not None and when < to_dt
        ]

    if sport_types:
        sport_set = {s for s in sport_types if s}
        if sport_set:
            activities = [a for a in activities if a.get("sport_type") in sport_set]

    def _within(field: str, lo: float | None, hi: float | None, multiplier: float = 1.0) -> None:
        nonlocal activities
        if lo is None and hi is None:
            return
        lo_v = lo * multiplier if lo is not None else None
        hi_v = hi * multiplier if hi is not None else None
        filtered = []
        for a in activities:
            v = a.get(field)
            v = float(v) if v is not None else 0.0
            if lo_v is not None and v < lo_v:
                continue
            if hi_v is not None and v > hi_v:
                continue
            filtered.append(a)
        activities = filtered

    _within("distance", distance_min_km, distance_max_km, multiplier=1000.0)
    _within("elevation_gain", elevation_min_m, elevation_max_m)
    _within("duration", duration_min_sec, duration_max_sec)
    _within("training_load", tss_min, tss_max)

    activities.sort(key=lambda a: a.get("date") or "", reverse=True)
    total = len(activities)
    start = (page - 1) * page_size
    end = start + page_size
    items = [_activity_to_summary(a) for a in activities[start:end]]
    return ActivitiesList(total=total, page=page, page_size=page_size, items=items)


@router.get("/sport-types", response_model=list[str])
def list_sport_types(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[str]:
    """Liste triée des ``sport_type`` distincts présents en base.

    Sert à peupler dynamiquement les chips de filtre côté frontend — ainsi on
    n'affiche que les sport types pour lesquels l'utilisateur a au moins une
    activité (pas la peine de proposer ``Swim`` si l'athlète ne nage pas).
    """
    activities = fetch_activities_from_db(ctx=ctx)
    return sorted({a["sport_type"] for a in activities if a.get("sport_type")})


@router.get("/{strava_id}", response_model=ActivityDetail)
def get_activity(
    strava_id: int,
    client: StravaClient = Depends(get_strava_client),  # noqa: B008
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivityDetail:
    """Détails complets d'une activité (streams inclus, cache 1 h)."""
    base = next(
        (a for a in fetch_activities_from_db(ctx=ctx) if a.get("strava_id") == strava_id),
        None,
    )
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {strava_id} introuvable en base.",
        )

    cache_key = (str(ctx.db_path), strava_id)
    now = time.time()
    with _cache_lock:
        cached = _detail_cache.get(cache_key)
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
            _detail_cache[cache_key] = (now, payload)
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


@router.get("/{strava_id}/similar", response_model=SimilarActivitiesResponse)
def get_similar_activities(
    strava_id: int,
    limit: int = Query(20, ge=1, le=100),
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SimilarActivitiesResponse:
    """Activités passées au profil similaire (distance + dénivelé + sport).

    Heuristique simple sans dépendance Strava : on retourne les activités du
    **même bucket de sport** (indoor/outdoor) dont la distance est à ±5 % et
    le dénivelé à ±10 % de la référence. Triées date desc.
    """
    raw = find_similar_activities(strava_id, limit=limit, db_path=ctx.db_path)
    return SimilarActivitiesResponse(**raw)
