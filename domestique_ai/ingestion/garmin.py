"""Ingestion Garmin Connect (API non officielle via ``garminconnect``).

Source d'ingestion des activités depuis 09/2026 (remplace Strava, dont l'API
exige un abonnement payant) : le compteur Edge / la montre Garmin synchronisent
vers Garmin Connect, et ce module rapatrie les activités dans la même table
``activities`` — toute la pipeline aval (TSS, CTL/ATL/TSB, zones HR, tendances,
coach LLM) fonctionne sans changement.

Conventions conservées :
- **Idempotence** sur ``garmin_id`` (index unique partiel, migration douce dans
  ``init_db``). Les lignes Garmin ont ``strava_id`` NULL.
- **Sync incrémentale** : la fenêtre par défaut démarre à la dernière activité
  Garmin connue (moins 1 j de marge), ``start_date=0``/ancien force le re-fetch.
- **Zones HR + température** calculées depuis les streams de
  ``get_activity_details`` quand HRrepos/HRmax sont configurés — mêmes helpers
  que l'ex-ingestion Strava (``calculate_hr_zones``, ``summarize_temp_stream``).

⚠️ Endpoints non officiels : peuvent changer sans préavis. Le parsing des
détails est défensif (orientation par descripteur ou par mesure) et logge le
payload brut en cas d'échec pour adaptation rapide.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import get_db_path, get_hr_max, get_hr_rest
from domestique_ai.ingestion.db import init_db, summarize_temp_stream
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_hr_zones,
    compute_training_load,
)

log = get_logger("garmin_ingest")

# Fenêtre par défaut du premier sync (historique complet) : 3 ans.
_DEFAULT_HISTORY_DAYS = 1095

# Mapping typeKey Garmin → sport_type (nomenclature historique type Strava).
# Les buckets indoor/outdoor du comparateur d'activités (similar.py)
# s'appuient sur ces noms.
_SPORT_MAP: dict[str, str] = {
    "cycling": "Ride",
    "virtual_ride": "VirtualRide",
    "indoor_cycling": "VirtualRide",
    "gravel_cycling": "GravelRide",
    "mountain_biking": "MountainBikeRide",
    "ebiking": "EBikeRide",
    "e_biking": "EBikeRide",
    "running": "Run",
    "trail_running": "TrailRun",
    "treadmill_running": "TreadmillRun",
    "hiking": "Hike",
    "walking": "Walk",
    "swimming": "Swim",
    "open_water_swimming": "OpenWaterSwim",
    "lap_swimming": "Swim",
    "fitness_equipment": "Workout",
    "yoga": "Yoga",
    "rowing": "Rowing",
    "rowing_indoor": "Rowing",
    "alpine_skiing": "AlpineSki",
    "snowshoeing": "Snowshoe",
    "elliptical": "Elliptical",
}


class GarminIngestError(RuntimeError):
    """Erreur d'ingestion Garmin Connect (auth, réseau, réponse inattendue)."""


def map_sport_type(type_key: str | None) -> str | None:
    """Convertit un ``typeKey`` Garmin en sport_type (nomenclature type Strava)."""
    if not type_key:
        return None
    mapped = _SPORT_MAP.get(type_key)
    if mapped:
        return mapped
    return type_key.replace("_", " ").title().replace(" ", "")


def _parse_gmt(timestamp: str | None) -> dt.datetime | None:
    """Parse un timestamp Garmin (``2026-08-30T08:12:33.0`` ou variante locale)."""
    if not timestamp:
        return None
    raw = timestamp.strip().replace(" ", "T")
    # Fractions de secondes tronquées à la microseconde pour fromisoformat.
    if "." in raw:
        head, _, tail = raw.partition(".")
        tail = "".join(c for c in tail if c.isdigit())[:6]
        raw = f"{head}.{tail}" if tail else head
    try:
        when = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def extract_activity_data(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Résume une activité Garmin (liste ``get_activities*``) au format interne.

    Même shape que l'ex-``StravaClient.extract_activity_data`` (Strava) : id,
    date (ISO UTC), duration (s), avg/max HR, avg_power, elevation, distance
    (m), sport_type.
    """
    if not raw:
        return None
    garmin_id = raw.get("activityId")
    if garmin_id is None:
        return None
    when = _parse_gmt(raw.get("startTimeGMT") or raw.get("startTimeLocal"))
    duration = raw.get("duration") or raw.get("elapsedDuration") or raw.get("movingDuration")
    activity_type = raw.get("activityType") or {}
    return {
        "id": garmin_id,
        "date": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
        "duration": duration,
        "avg_heart_rate": raw.get("averageHR"),
        "max_heart_rate": raw.get("maxHR"),
        "avg_power": raw.get("averagePower"),
        "elevation_gain": raw.get("elevationGain"),
        "distance": raw.get("distance"),
        "sport_type": map_sport_type(
            activity_type.get("typeKey") if isinstance(activity_type, dict) else None
        ),
        "map_polyline": None,
    }


# ---------------------------------------------------------------------------
# Parsing défensif des streams (get_activity_details)
# ---------------------------------------------------------------------------


def _find_metrics_entries(obj: Any) -> list[dict[str, Any]] | None:
    """Trouve récursivement une liste d'entrées ``{'metrics': [...]}``."""
    if isinstance(obj, list) and obj and all(isinstance(e, dict) and "metrics" in e for e in obj):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_metrics_entries(value)
            if found:
                return found
    return None


_TIME_KEYS = ("seconds", "elapsedseconds", "elapsed_duration", "timeremaininguntilnextcalendaryear")
_HR_KEYS = ("directheartrate", "heartrate")
_TEMP_KEYS = ("directairtemperature", "airtemperature", "temperature")
_POWER_KEYS = ("directdoublepower", "directpower", "power", "watts")


def _match_key(descriptors: list[dict[str, Any]], candidates: tuple[str, ...]) -> int | None:
    """Indice du descripteur dont ``key`` (normalisé) figure dans candidates."""
    for idx, desc in enumerate(descriptors):
        key = str(desc.get("key") or desc.get("metricKey") or "").lower()
        if key in candidates:
            return idx
    return None


def parse_details_streams(details: dict[str, Any] | None) -> dict[str, list[float]] | None:
    """Extrait les séries HR / temps / température d'un payload de détails.

    L'endpoint ``/activity/{id}/details`` renvoie des ``metricsEntries`` dont
    l'orientation varie : soit une entrée par métrique (``metrics`` = une valeur
    par échantillon), soit une entrée par échantillon (``metrics`` = une valeur
    par métrique, colonnes décrites par ``metricDescriptorDTOs``). Les deux
    orientations sont gérées.

    Retourne ``{"heartrate": [...], "time": [...], "temp": [...]}`` (clés
    absentes omises), ou ``None`` si rien d'exploitable.
    """
    if not details:
        return None
    entries = _find_metrics_entries(details)
    if not entries:
        return None

    # Orientation A : une entrée par métrique (1 descripteur, N valeurs).
    columns: dict[str, list[float]] = {}
    for entry in entries:
        descriptors = entry.get("metricDescriptorDTOs") or []
        values = entry.get("metrics") or []
        if len(descriptors) == 1 and values:
            key = str(descriptors[0].get("key") or "").lower()
            columns[key] = [float(v) for v in values if v is not None]

    hr = columns.get("directheartrate") or columns.get("heartrate")
    temp = columns.get("directairtemperature") or columns.get("airtemperature")
    time_series = columns.get("seconds") or columns.get("elapsedseconds")

    # Orientation B : une entrée par échantillon, colonnes par descripteur.
    if hr is None:
        first = entries[0]
        descriptors = first.get("metricDescriptorDTOs") or []
        hr_idx = _match_key(descriptors, _HR_KEYS)
        temp_idx = _match_key(descriptors, _TEMP_KEYS)
        time_idx = _match_key(descriptors, _TIME_KEYS)
        if hr_idx is not None:
            hr = [
                float(e["metrics"][hr_idx])
                for e in entries
                if len(e.get("metrics") or []) > hr_idx and e["metrics"][hr_idx] is not None
            ]
            if temp_idx is not None:
                temp = [
                    float(e["metrics"][temp_idx])
                    for e in entries
                    if len(e.get("metrics") or []) > temp_idx and e["metrics"][temp_idx] is not None
                ]
            if time_idx is not None:
                time_series = [
                    float(e["metrics"][time_idx])
                    for e in entries
                    if len(e.get("metrics") or []) > time_idx and e["metrics"][time_idx] is not None
                ]

    if not hr:
        return None
    streams: dict[str, list[float]] = {"heartrate": hr}
    if time_series:
        streams["time"] = time_series
    if temp:
        streams["temp"] = temp
    return streams


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def save_garmin_activity(
    activity: dict[str, Any],
    db_path: Path | None = None,
    ftp: float | None = None,
    hr_zones: dict[str, float] | None = None,
    temp_summary: tuple[float, float, float] | None = None,
    *,
    ctx: AthleteContext | None = None,
) -> bool:
    """Sauvegarde une activité Garmin. ``True`` si insérée, ``False`` si doublon.

    Miroir de l'ex-``save_activity`` (Strava) : TSS calculé si absent (hr-TSS
    prioritaire), zones HR et température écrites si fournies, idempotence sur
    ``garmin_id``.
    """
    garmin_id = activity.get("id")
    if garmin_id is None:
        return False
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())

    tss = activity.get("training_load")
    if tss is None:
        try:
            duration = int(activity.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            avg_power = float(activity.get("avg_power") or 0.0)
        except (TypeError, ValueError):
            avg_power = 0.0
        try:
            avg_hr = float(activity.get("avg_heart_rate") or 0.0)
        except (TypeError, ValueError):
            avg_hr = 0.0
        tss = compute_training_load(
            duration_sec=duration,
            avg_hr=avg_hr or None,
            avg_power=avg_power or None,
            ftp=ftp if ftp is not None else (ctx.ftp if ctx else None),
            hr_rest=ctx.hr_rest if ctx else None,
            hr_max=ctx.hr_max if ctx else None,
            sex=ctx.sex if ctx else None,
            lthr_pct=ctx.lthr_pct if ctx else None,
        )

    zone_values = (
        tuple(hr_zones.get(key) for key in HR_ZONE_KEYS) if hr_zones is not None else (None,) * 5
    )
    temp_values = temp_summary if temp_summary is not None else (None, None, None)

    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute("SELECT 1 FROM activities WHERE garmin_id = ?", (garmin_id,))
        if cursor.fetchone():
            return False
        conn.execute(
            """
            INSERT INTO activities (
                strava_id, garmin_id, date, duration, avg_heart_rate, max_heart_rate,
                avg_power, elevation_gain, distance, training_load,
                hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time,
                sport_type, avg_temp, min_temp, max_temp, map_polyline
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                None,
                garmin_id,
                activity.get("date"),
                activity.get("duration"),
                activity.get("avg_heart_rate"),
                activity.get("max_heart_rate"),
                activity.get("avg_power"),
                activity.get("elevation_gain"),
                activity.get("distance"),
                tss,
                *zone_values,
                activity.get("sport_type"),
                *temp_values,
                activity.get("map_polyline"),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _last_garmin_activity_date(
    db_path: Path | None = None, *, ctx: AthleteContext | None = None
) -> dt.date | None:
    """Date (UTC) de la dernière activité Garmin connue, ``None`` si vide."""
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM activities WHERE garmin_id IS NOT NULL"
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    when = _parse_gmt(row[0])
    return when.date() if when else None


# ---------------------------------------------------------------------------
# Client + sync
# ---------------------------------------------------------------------------


def get_ingest_client(token_dir: Path | None = None) -> Any:
    """Client ``garminconnect`` authentifié, réutilisant le cache token du push.

    Lève ``GarminIngestError`` avec un message actionnable si les credentials
    ou le cache token manquent (seed interactif requis — MFA inclus).
    """
    from domestique_ai.export.garmin_connect import (
        GarminPushError,
        credentials_present,
        get_client,
        token_cache_present,
    )

    if not credentials_present():
        raise GarminIngestError(
            "GARMIN_EMAIL / GARMIN_PASSWORD absents du .env — requis pour l'ingestion Garmin."
        )
    if not token_cache_present():
        raise GarminIngestError(
            "Pas de tokens Garmin Connect — lance `python -m domestique_ai.export.garmin_connect` "
            "une fois (login interactif, MFA inclus) pour les initialiser."
        )
    try:
        return get_client(token_dir=token_dir)
    except GarminPushError as exc:
        raise GarminIngestError(
            f"Authentification Garmin échouée — re-seed le token : {exc}"
        ) from exc


def sync_activities_garmin(
    client: Any | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> int:
    """Récupère et sauvegarde les activités Garmin. Retourne le nombre d'insertions.

    Sync incrémentale par défaut : ``start_date`` non fourni → 1 j avant la
    dernière activité Garmin en base (ou l'historique ``_DEFAULT_HISTORY_DAYS``
    au premier sync). Les streams (zones HR, température) sont récupérés via
    ``get_activity_details`` pour chaque activité avec HR — 1 appel par
    activité, uniquement si HRrepos/HRmax sont configurés.
    """
    if end_date is None:
        end_date = dt.date.today()
    if start_date is None:
        last = _last_garmin_activity_date(db_path, ctx=ctx)
        if last is not None:
            start_date = last - dt.timedelta(days=1)
        else:
            start_date = end_date - dt.timedelta(days=_DEFAULT_HISTORY_DAYS)
    if start_date > end_date:
        return 0

    init_db(db_path, ctx=ctx)
    if client is None:
        client = get_ingest_client()

    hr_rest = ctx.hr_rest if ctx else get_hr_rest()
    hr_max = ctx.hr_max if ctx else get_hr_max()
    zone_params: tuple[float, float] | None = (
        (float(hr_rest), float(hr_max)) if hr_rest and hr_max and hr_max > hr_rest else None
    )

    try:
        raws = client.get_activities_by_date(
            start_date.isoformat(), (end_date + dt.timedelta(days=1)).isoformat()
        )
    except Exception as exc:  # noqa: BLE001 — remap avec contexte
        raise GarminIngestError(f"Lecture des activités Garmin échouée : {exc}") from exc

    inserted = 0
    for raw in raws:
        data = extract_activity_data(raw)
        if data is None:
            continue
        zones: dict[str, float] | None = None
        temp_summary: tuple[float, float, float] | None = None
        if zone_params and data.get("avg_heart_rate") and data.get("id"):
            try:
                details = client.get_activity_details(data["id"])
            except Exception:  # noqa: BLE001 — une activité KO n'arrête pas la sync
                log.warning("Garmin %s : détails indisponibles.", data["id"], exc_info=True)
                details = None
            if details:
                streams = parse_details_streams(details)
                if streams:
                    hr_stream = streams.get("heartrate")
                    time_stream = streams.get("time")
                    if hr_stream and time_stream:
                        zones = calculate_hr_zones(hr_stream, time_stream, *zone_params)
                    temp_summary = summarize_temp_stream(streams.get("temp"))
                else:
                    log.warning(
                        "Garmin %s : streams non extractibles des détails — payload : %s",
                        data["id"],
                        json.dumps(details)[:400],
                    )
        if save_garmin_activity(
            data, hr_zones=zones, temp_summary=temp_summary, ctx=ctx, db_path=db_path
        ):
            inserted += 1

    log.info(
        "Sync Garmin %s → %s : %d insertions (%d activités lues).",
        start_date,
        end_date,
        inserted,
        len(raws),
    )
    return inserted
