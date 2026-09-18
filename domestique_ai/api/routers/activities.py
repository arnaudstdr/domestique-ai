"""Endpoints de listing et détail des activités."""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    ActivitiesList,
    ActivityCreate,
    ActivityDetail,
    ActivityStreams,
    ActivitySummary,
    ActivityUpdate,
    ActivityWeather,
    SimilarActivitiesResponse,
    TcxImportFileResult,
    TcxImportResponse,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import (
    activity_id_for_source_uid,
    delete_activity,
    insert_activity,
    load_activity_streams,
    store_activity_streams,
    update_activity_fields,
)
from domestique_ai.ingestion.garmin import (
    GarminIngestError,
    get_ingest_client,
    parse_activity_weather,
    parse_details_series,
)
from domestique_ai.ingestion.tcx import TcxActivity, TcxParseError, parse_tcx
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_hr_zones,
    compute_training_load,
    fetch_activities_from_db,
)
from domestique_ai.processing.similar import find_similar_activities

router = APIRouter(prefix="/api/activities", tags=["activities"])
log = get_logger("activities")

# Cache mémoire des streams (fetch live Garmin, pattern ex-Strava : les streams
# ne sont pas persistés en base). TTL 1 h — clé (db_path, external_id).
_STREAMS_TTL_SEC = 3600
_streams_cache: dict[tuple[str, int], tuple[float, ActivityStreams]] = {}
_streams_lock = threading.Lock()


def _kmh_from_ms(value: float | None) -> float | None:
    """m/s → km/h arrondi 0.1 (``None`` préservé)."""
    if value is None:
        return None
    return round(float(value) * 3.6, 1)


def _external_id_of(row: dict) -> int:
    """Id externe exposé : strava_id (legacy), sinon garmin_id, sinon id local.

    Les activités saisies à la main ou importées (TCX) n'ont ni strava_id ni
    garmin_id — l'id autoincrement local fait office d'``external_id``. Les ids
    externes (Garmin/Strava) sont des entiers à ~10 chiffres, très au-dessus des
    ids locaux : pas de collision en pratique.
    """
    for key in ("strava_id", "garmin_id", "id"):
        value = row.get(key)
        if value is not None:
            return int(value)
    raise ValueError("Activité sans identifiant exploitable.")


def _activity_to_summary(row: dict) -> ActivitySummary:
    distance_m = row.get("distance") or 0
    source = row.get("source")
    if source is None:
        source = (
            "garmin"
            if row.get("strava_id") is None and row.get("garmin_id") is not None
            else "strava"
        )
    return ActivitySummary(
        external_id=_external_id_of(row),
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
        calories=row.get("calories"),
        max_power=row.get("max_power"),
        cadence_avg=row.get("cadence_avg"),
        cadence_max=row.get("cadence_max"),
        speed_avg_kmh=_kmh_from_ms(row.get("speed_avg")),
        speed_max_kmh=_kmh_from_ms(row.get("speed_max")),
        elevation_loss=row.get("elevation_loss"),
        source=source,
        notes=row.get("notes"),
        rpe=row.get("rpe"),
    )


def _clean_text(value: str | None) -> str | None:
    """Normalise un champ texte éditable : ``strip`` et chaîne vide → ``None``."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


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


def _find_activity(external_id: int, ctx: AthleteContext) -> dict | None:
    """Retrouve une ligne activité par strava_id, garmin_id ou id local."""
    return next(
        (a for a in fetch_activities_from_db(ctx=ctx) if _external_id_of(a) == external_id),
        None,
    )


@router.get("/{external_id}", response_model=ActivityDetail)
def get_activity(
    external_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivityDetail:
    """Détails d'une activité depuis la base locale (zones HR, TSS, temp.).

    L'id passé en path peut être un strava_id historique, un garmin_id, ou
    l'id local d'une activité manuelle/TCX. Les streams bruts (courbes HR,
    watts, altitude, carte) sont servis par l'endpoint ``/streams`` dédié
    (fetch live Garmin ou streams persistés pour les imports TCX).
    """
    base = _find_activity(external_id, ctx)
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {external_id} introuvable en base.",
        )

    activity = _activity_to_summary(base)

    hr_zones = {key: base.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
    has_hr_zones = any(v is not None for v in hr_zones.values())

    return ActivityDetail(
        activity=activity,
        streams=ActivityStreams(),
        hr_zones={k: float(v) for k, v in hr_zones.items() if v is not None}
        if has_hr_zones
        else None,
    )


def _resolve_activity(external_id: int, ctx: AthleteContext) -> tuple[dict, int | None]:
    """Résout un external_id (strava legacy, garmin, ou id local) en ligne + garmin_id.

    Lève 404 si l'activité est introuvable. ``garmin_id`` vaut ``None`` pour une
    ligne historique Strava ou une activité manuelle/importée.
    """
    base = _find_activity(external_id, ctx)
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {external_id} introuvable en base.",
        )
    garmin_id = base.get("garmin_id")
    return base, int(garmin_id) if garmin_id is not None else None


@router.get("/{external_id}/streams", response_model=ActivityStreams)
def get_activity_streams(
    external_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivityStreams:
    """Streams d'une activité (HR, puissance, altitude, GPS…).

    Deux sources :

    - **Garmin** : fetch live ``get_activity_details`` (polyline incluse via
      ``maxpoly``), parsing défensif multi-orientation, cache mémoire 1 h — les
      streams Garmin ne sont pas persistés.
    - **Persistés** : activités importées (TCX). On lit la table
      ``activity_streams`` ; les activités manuelles n'en ont pas → 404.
    """
    base, garmin_id = _resolve_activity(external_id, ctx)
    if garmin_id is None:
        persisted = load_activity_streams(base["id"], ctx=ctx)
        if persisted:
            return ActivityStreams(**persisted)
        if base.get("strava_id") is not None:
            detail = (
                f"Streams indisponibles pour l'activité {external_id} : "
                "historique Strava, l'API Strava n'est plus interrogée depuis 09/2026."
            )
        else:
            detail = (
                f"Streams indisponibles pour l'activité {external_id} : "
                "aucun stream importé (activité manuelle)."
            )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

    cache_key = (str(ctx.db_path), external_id)
    now = time.time()
    with _streams_lock:
        cached = _streams_cache.get(cache_key)
        if cached and now - cached[0] < _STREAMS_TTL_SEC:
            return cached[1]

    try:
        client = get_ingest_client()
        details = client.get_activity_details(str(garmin_id), maxpoly=1000)
    except GarminIngestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — remap réseau/API en 503
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Lecture des streams Garmin échouée : {exc}",
        ) from exc

    series = parse_details_series(details)
    streams = ActivityStreams(
        time=[int(v) for v in series["time"]] if series.get("time") else None,
        heartrate=series.get("heartrate"),
        altitude=series.get("altitude"),
        watts=series.get("power"),
        latlng=series.get("latlng"),
        cadence=series.get("cadence"),
        velocity_smooth=series.get("speed"),
        distance=series.get("distance"),
        temp=series.get("temp"),
    )

    with _streams_lock:
        _streams_cache[cache_key] = (time.time(), streams)
    return streams


@router.get("/{external_id}/weather", response_model=ActivityWeather)
def get_activity_weather_endpoint(
    external_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivityWeather:
    """Météo au lieu/heure de l'activité Garmin (best-effort).

    Complément des températures capteur : utile pour les activités sans sonde
    (home trainer exclu — pas de GPS, Garmin ne renvoie rien →
    ``available: false``). Toute erreur Garmin → ``available: false`` plutôt
    qu'une 5xx, la carte météo ne doit jamais casser la page.
    """
    base, garmin_id = _resolve_activity(external_id, ctx)
    if garmin_id is None:
        # Pas de source Garmin : pas de météo à récupérer (activité Strava
        # legacy, manuelle ou importée). Le front ignore silencieusement.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Météo indisponible pour l'activité {external_id} : source non Garmin.",
        )

    try:
        client = get_ingest_client()
        raw = client.get_activity_weather(str(garmin_id))
    except GarminIngestError as exc:
        log.warning("Météo %s : Garmin injoignable (%s).", external_id, exc)
        return ActivityWeather(available=False)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Météo %s indisponible : %s", external_id, exc)
        return ActivityWeather(available=False)

    return ActivityWeather(**parse_activity_weather(raw))


@router.get("/{external_id}/similar", response_model=SimilarActivitiesResponse)
def get_similar_activities(
    external_id: int,
    limit: int = Query(20, ge=1, le=100),
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SimilarActivitiesResponse:
    """Activités passées au profil similaire (distance + dénivelé + sport).

    Heuristique simple sans dépendance distante : on retourne les activités du
    **même bucket de sport** (indoor/outdoor) dont la distance est à ±5 % et
    le dénivelé à ±10 % de la référence. Triées date desc.
    """
    raw = find_similar_activities(external_id, limit=limit, db_path=ctx.db_path)
    return SimilarActivitiesResponse(**raw)


def _summary_of_id(activity_id: int, ctx: AthleteContext) -> ActivitySummary:
    row = _find_activity(activity_id, ctx)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Activité écrite mais introuvable en base.",
        )
    return _activity_to_summary(row)


@router.post("", response_model=ActivitySummary, status_code=status.HTTP_201_CREATED)
def create_activity(
    payload: ActivityCreate,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivitySummary:
    """Ajoute une activité saisie à la main (source ``manual``).

    Le TSS est calculé côté serveur selon le profil de l'athlète (hr-TSS
    prioritaire, sinon TSS puissance, sinon 0). Aucun stream n'est disponible —
    la page détail n'affichera que les métriques.
    """
    try:
        when = _parse_iso_utc(payload.date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"date invalide : {payload.date!r}",
        ) from exc

    tss = compute_training_load(
        duration_sec=payload.duration_sec,
        avg_hr=payload.avg_hr,
        avg_power=payload.avg_power,
        ftp=ctx.ftp,
        hr_rest=ctx.hr_rest,
        hr_max=ctx.hr_max,
        sex=ctx.sex,
        lthr_pct=ctx.lthr_pct,
    )
    new_id = insert_activity(
        {
            "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": int(payload.duration_sec),
            "distance": float(payload.distance_km) * 1000.0,
            "elevation_gain": payload.elevation_m,
            "avg_heart_rate": payload.avg_hr,
            "max_heart_rate": payload.max_hr,
            "avg_power": payload.avg_power,
            "training_load": tss,
            "sport_type": payload.sport_type or "Ride",
            "name": payload.name or "Séance manuelle",
            "notes": _clean_text(payload.notes),
            "rpe": payload.rpe,
            "source": "manual",
        },
        ctx=ctx,
    )
    return _summary_of_id(new_id, ctx)


@router.patch("/{external_id}", response_model=ActivitySummary)
def update_activity_endpoint(
    external_id: int,
    payload: ActivityUpdate,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ActivitySummary:
    """Modifie les champs éditables d'une activité (nom, type, commentaire, RPE).

    Ouvert à **toutes les sources** : l'édition ne touche que des champs jamais
    régénérés par l'ingestion (le sync Garmin n'écrase pas une ligne existante).
    Un ``PATCH`` partiel ne modifie que les champs fournis ; ``null`` efface.
    """
    base = _find_activity(external_id, ctx)
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {external_id} introuvable en base.",
        )

    fields = payload.model_dump(exclude_unset=True)
    for key in ("name", "sport_type", "notes"):
        if key in fields:
            fields[key] = _clean_text(fields[key])
    if not fields:
        return _activity_to_summary(base)

    update_activity_fields(base["id"], fields, ctx=ctx)
    with _streams_lock:
        _streams_cache.pop((str(ctx.db_path), external_id), None)
    updated = _find_activity(external_id, ctx)
    return _activity_to_summary(updated or base)


def _persist_tcx_activity(
    parsed: TcxActivity, source_uid: str, ctx: AthleteContext
) -> ActivitySummary:
    """Persiste une activité TCX (agrégats + TSS/zones + streams)."""
    tss = compute_training_load(
        duration_sec=parsed.duration,
        avg_hr=parsed.avg_heart_rate,
        avg_power=parsed.avg_power,
        ftp=ctx.ftp,
        hr_rest=ctx.hr_rest,
        hr_max=ctx.hr_max,
        sex=ctx.sex,
        lthr_pct=ctx.lthr_pct,
    )
    record: dict = {
        "date": parsed.date,
        "duration": parsed.duration,
        "distance": parsed.distance,
        "elevation_gain": parsed.elevation_gain,
        "elevation_loss": parsed.elevation_loss,
        "avg_heart_rate": parsed.avg_heart_rate,
        "max_heart_rate": parsed.max_heart_rate,
        "avg_power": parsed.avg_power,
        "max_power": parsed.max_power,
        "cadence_avg": parsed.cadence_avg,
        "cadence_max": parsed.cadence_max,
        "speed_avg": parsed.speed_avg,
        "speed_max": parsed.speed_max,
        "calories": parsed.calories,
        "start_lat": parsed.start_lat,
        "start_lng": parsed.start_lng,
        "map_polyline": parsed.map_polyline,
        "sport_type": parsed.sport_type,
        "name": parsed.name,
        "training_load": tss,
        "source": "tcx",
        "source_uid": source_uid,
    }
    if ctx.hr_rest and ctx.hr_max and parsed.zone_hr_stream:
        zones = calculate_hr_zones(
            parsed.zone_hr_stream,
            parsed.zone_time_stream,
            float(ctx.hr_rest),
            float(ctx.hr_max),
        )
        for key in HR_ZONE_KEYS:
            record[f"hr_{key}_time"] = zones.get(key)

    new_id = insert_activity(record, ctx=ctx)
    store_activity_streams(new_id, parsed.streams, ctx=ctx)
    return _summary_of_id(new_id, ctx)


@router.post("/import/tcx", response_model=TcxImportResponse)
def import_tcx_activities(
    files: list[UploadFile] = File(...),  # noqa: B008
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> TcxImportResponse:
    """Importe un ou plusieurs fichiers TCX (source ``tcx``).

    Dédup par hash sha1 du contenu (clé ``<hash>:<index>`` pour les fichiers
    multi-activités). Un fichier en erreur n'interrompt pas les autres — le
    résultat est détaillé fichier par fichier.
    """
    results: list[TcxImportFileResult] = []
    imported = skipped = error_count = 0

    for upload in files:
        filename = (upload.filename or "fichier.tcx").strip() or "fichier.tcx"
        try:
            content = upload.file.read()
        except Exception as exc:  # noqa: BLE001 — entrée illisible
            error_count += 1
            results.append(
                TcxImportFileResult(
                    filename=filename, status="error", reason=f"Lecture impossible : {exc}"
                )
            )
            continue

        digest = hashlib.sha1(content).hexdigest()
        if activity_id_for_source_uid(f"{digest}:0", ctx=ctx) is not None:
            skipped += 1
            results.append(
                TcxImportFileResult(
                    filename=filename, status="skipped", reason="Fichier déjà importé."
                )
            )
            continue

        try:
            parsed_list = parse_tcx(content)
        except TcxParseError as exc:
            error_count += 1
            results.append(TcxImportFileResult(filename=filename, status="error", reason=str(exc)))
            continue

        try:
            summaries = [
                _persist_tcx_activity(parsed, f"{digest}:{index}", ctx)
                for index, parsed in enumerate(parsed_list)
            ]
        except Exception as exc:  # noqa: BLE001 — une écriture ratée ne bloque pas le lot
            log.warning("Import TCX %s échoué : %s", filename, exc)
            error_count += 1
            results.append(
                TcxImportFileResult(
                    filename=filename, status="error", reason=f"Écriture impossible : {exc}"
                )
            )
            continue

        imported += len(summaries)
        results.append(
            TcxImportFileResult(filename=filename, status="imported", activities=summaries)
        )

    return TcxImportResponse(
        imported=imported, skipped=skipped, errors=error_count, results=results
    )


@router.delete("/{external_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity_endpoint(
    external_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> None:
    """Supprime une activité manuelle ou importée (annulation d'un mauvais import).

    Les activités Garmin/Strava sont refusées (403) : leur suppression serait
    re-créée au prochain sync.
    """
    base = _find_activity(external_id, ctx)
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activité {external_id} introuvable en base.",
        )
    if base.get("source") not in ("manual", "tcx"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seules les activités manuelles ou importées peuvent être supprimées.",
        )
    delete_activity(base["id"], ctx=ctx)
