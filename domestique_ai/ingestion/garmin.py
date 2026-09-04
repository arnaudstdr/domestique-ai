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
from domestique_ai.ingestion.db import (
    get_sync_meta,
    init_db,
    set_sync_meta,
    summarize_temp_stream,
)
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
    "road_biking": "Ride",
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


def _num(raw: dict[str, Any], *keys: str) -> float | None:
    """Premier champ numérique présent (None-check explicite : 0.0 légitime)."""
    for key in keys:
        value = raw.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def extract_activity_data(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Résume une activité Garmin (liste ``get_activities*``) au format interne.

    Même shape que l'ex-``StravaClient.extract_activity_data`` (Strava) : id,
    date (ISO UTC), duration (s), avg/max HR, avg_power, elevation, distance
    (m), sport_type — enrichie (09/2026) des champs du payload liste :
    ``activityName``, ``calories``, ``maxPower``, cadence (moy/max, unités
    hétérogènes bike rpm / run pas-min stockées telles quelles), vitesse
    moyenne/max (m/s), D−, point de départ. Tous ces champs sont défensifs
    (absents selon device/sport → ``None``).
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
        "name": raw.get("activityName"),
        "date": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
        "duration": duration,
        "avg_heart_rate": raw.get("averageHR"),
        "max_heart_rate": raw.get("maxHR"),
        "avg_power": raw.get("averagePower"),
        "max_power": raw.get("maxPower"),
        "elevation_gain": raw.get("elevationGain"),
        "elevation_loss": raw.get("elevationLoss"),
        "distance": raw.get("distance"),
        "calories": raw.get("calories"),
        "cadence_avg": _num(
            raw,
            "averageBikingCadenceInRevPerMinute",
            "averageRunningCadenceInStepsPerMinute",
        ),
        "cadence_max": _num(
            raw,
            "maxBikingCadenceInRpm",
            "maxRunningCadenceInStepsPerMinute",
        ),
        "speed_avg": raw.get("averageSpeed"),
        "speed_max": raw.get("maxSpeed"),
        "start_lat": raw.get("startLatitude"),
        "start_lng": raw.get("startLongitude"),
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


_TIME_KEYS = ("sumelapsedduration", "sumduration", "seconds", "elapsedseconds", "elapsed_duration")
_HR_KEYS = ("directheartrate", "heartrate")
_TEMP_KEYS = ("directairtemperature", "airtemperature", "temperature")
_POWER_KEYS = ("directdoublepower", "directpower", "power", "watts")
_ALTITUDE_KEYS = ("directelevation", "directuncorrectedelevation", "altitude", "elevation")
_SPEED_KEYS = ("directspeed", "speed")
_DISTANCE_KEYS = ("sumdistance", "distance")
_CADENCE_KEYS = ("directbikingcadence", "directcadence", "cadence")
_LAT_KEYS = ("directlatitude", "latitude")
_LNG_KEYS = ("directlongitude", "longitude")

# Candidats de clés (normalisées minuscules) par série, dans l'ordre de
# priorité. ``directtimestamp`` (epoch ms) est converti en secondes relatives
# pendant l'extraction des colonnes.
_SERIES_KEYS: dict[str, tuple[str, ...]] = {
    "heartrate": _HR_KEYS,
    "time": _TIME_KEYS,
    "temp": _TEMP_KEYS,
    "power": _POWER_KEYS,
    "altitude": _ALTITUDE_KEYS,
    "speed": _SPEED_KEYS,
    "distance": _DISTANCE_KEYS,
    "cadence": _CADENCE_KEYS,
    "lat": _LAT_KEYS,
    "lng": _LNG_KEYS,
}


def _match_key(descriptors: list[dict[str, Any]], candidates: tuple[str, ...]) -> int | None:
    """Indice du descripteur dont ``key`` (normalisé) figure dans candidates."""
    for idx, desc in enumerate(descriptors):
        key = str(desc.get("key") or desc.get("metricKey") or "").lower()
        if key in candidates:
            return idx
    return None


def _extract_columns_modern(details: dict[str, Any]) -> dict[str, list[float]]:
    """Orientation « moderne » : ``metricDescriptors`` (metricsIndex) top-level
    + ``activityDetailMetrics`` (une entrée par échantillon, valeurs positionnelles).

    ``directTimestamp`` (epoch ms) est normalisé en secondes relatives au
    départ. Retourne un dict clé de métrique → colonne de valeurs.
    """
    index_to_key: dict[int, str] = {}
    for desc in details.get("metricDescriptors") or []:
        key = desc.get("key")
        idx = desc.get("metricsIndex")
        if not isinstance(key, str) or not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            continue
        index_to_key[idx] = key.lower()

    samples = details.get("activityDetailMetrics") or []
    if not samples:
        return {}
    columns: dict[str, list[float]] = {}
    raw_ts: list[float] = []
    for sample in samples:
        metrics = sample.get("metrics") or []
        for idx, key in index_to_key.items():
            if idx >= len(metrics):
                continue
            value = metrics[idx]
            if value is None:
                continue
            if key == "directtimestamp":
                raw_ts.append(float(value))
            else:
                columns.setdefault(key, []).append(float(value))
    if raw_ts:
        # Epoch ms → secondes relatives au 1er sample.
        t0 = raw_ts[0]
        columns["directtimestamp"] = [(v - t0) / 1000.0 for v in raw_ts]
    return columns


def _extract_columns_legacy(entries: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Orientation « legacy » : liste d'entrées ``{'metrics': [...]}``.

    Deux sous-orientations :
    - A : une entrée par métrique (1 descripteur, N valeurs) ;
    - B : une entrée par échantillon, colonnes décrites par ``metricDescriptorDTOs``.
    """
    columns: dict[str, list[float]] = {}
    for entry in entries:
        descriptors = entry.get("metricDescriptorDTOs") or []
        values = entry.get("metrics") or []
        if len(descriptors) == 1 and values:
            key = str(descriptors[0].get("key") or "").lower()
            columns.setdefault(key, []).extend(float(v) for v in values if v is not None)
    if columns:
        return columns

    first = entries[0]
    descriptors = first.get("metricDescriptorDTOs") or []
    for idx, desc in enumerate(descriptors):
        key = str(desc.get("key") or "").lower()
        column = [
            float(e["metrics"][idx])
            for e in entries
            if len(e.get("metrics") or []) > idx and e["metrics"][idx] is not None
        ]
        if column:
            columns[key] = column
    return columns


def parse_details_series(details: dict[str, Any] | None) -> dict[str, list[float]]:
    """Extrait toutes les séries exploitables d'un payload de détails Garmin.

    L'endpoint ``/activity/{id}/details`` présente plusieurs orientations de
    payload (moderne : ``metricDescriptors`` + ``activityDetailMetrics`` ;
    legacy : ``metricsEntries`` par métrique ou par échantillon). Toutes sont
    normalisées en colonnes par clé de descripteur, puis mappées sur les
    séries attendues via ``_SERIES_KEYS``.

    Retourne un dict de séries disponibles parmi : ``heartrate``, ``time``,
    ``temp``, ``power``, ``altitude``, ``speed``, ``distance``, ``cadence``,
    ``latlng`` (paires [lat, lon]). Les séries lat/lng sont zippées — seuls les
    échantillons complets sont gardés.
    """
    if not details:
        return {}

    if details.get("metricDescriptors") and details.get("activityDetailMetrics"):
        columns = _extract_columns_modern(details)
    else:
        entries = _find_metrics_entries(details)
        columns = _extract_columns_legacy(entries) if entries else {}
    if not columns:
        return {}

    series: dict[str, list[float]] = {}
    for name, candidates in _SERIES_KEYS.items():
        for candidate in candidates:
            column = columns.get(candidate)
            if column:
                series[name] = column
                break

    lat = series.pop("lat", None)
    lng = series.pop("lng", None)
    if lat and lng:
        series["latlng"] = [[a, o] for a, o in zip(lat, lng, strict=False)]
    return series


def parse_details_streams(details: dict[str, Any] | None) -> dict[str, list[float]] | None:
    """Séries HR / temps / température d'un payload de détails (zones HR, temp).

    Wrapper minimal au-dessus de ``parse_details_series`` conservé pour le
    calcul des zones HR à l'ingestion. Retourne ``None`` si pas de HR
    exploitable.
    """
    series = parse_details_series(details)
    if not series.get("heartrate"):
        return None
    streams: dict[str, list[float]] = {"heartrate": series["heartrate"]}
    if series.get("time"):
        streams["time"] = series["time"]
    if series.get("temp"):
        streams["temp"] = series["temp"]
    return streams


# ---------------------------------------------------------------------------
# Polyline (tracé GPS → aperçu des cartes de la liste)
# ---------------------------------------------------------------------------


def _encode_polyline_value(value: int) -> str:
    """Encode un delta signé en varint base64-like (offset 63), format Google."""
    value = ~(value << 1) if value < 0 else value << 1
    chunks: list[str] = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode_polyline(points: list[tuple[float, float]]) -> str | None:
    """Encode une liste de ``(lat, lng)`` en polyline encodée Google.

    Même format que le ``summary_polyline`` Strava (delta-encodage + varint,
    précision 1e-5) — consommé tel quel par le composant ``RoutePreview`` du
    frontend. ``None`` si moins de 2 points.
    """
    if not points or len(points) < 2:
        return None
    out: list[str] = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = round(lat * 1e5), round(lng * 1e5)
        out.append(_encode_polyline_value(ilat - prev_lat))
        out.append(_encode_polyline_value(ilng - prev_lng))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


def _polyline_from_details(details: dict[str, Any] | None) -> list[tuple[float, float]]:
    """Points (lat, lng) du tracé GPS dans ``geoPolylineDTO.polyline``."""
    if not details:
        return []
    geo = details.get("geoPolylineDTO") or {}
    if not isinstance(geo, dict):
        return []
    points: list[tuple[float, float]] = []
    for p in geo.get("polyline") or []:
        if not isinstance(p, dict):
            continue
        lat, lng = p.get("lat"), p.get("lon")
        if lat is None or lng is None:
            continue
        points.append((float(lat), float(lng)))
    return points


def _downsample_points(
    points: list[tuple[float, float]], max_points: int = 300
) -> list[tuple[float, float]]:
    """Sous-échantillonne régulièrement la liste de points (aperçu carte)."""
    n = len(points)
    if n <= max_points:
        return points
    step = n / max_points
    return [points[int(i * step)] for i in range(max_points)]


# ---------------------------------------------------------------------------
# Météo (get_activity_weather)
# ---------------------------------------------------------------------------


def _f_to_c(value: Any) -> float | None:
    """Fahrenheit → Celsius arrondi 0.1 (payload weather Garmin en °F)."""
    if value is None:
        return None
    try:
        return round((float(value) - 32.0) * 5.0 / 9.0, 1)
    except (TypeError, ValueError):
        return None


def parse_activity_weather(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise le payload météo Garmin (temp en °F → °C, dicts imbriqués).

    Retourne un dict sérialisable tel quel pour ``ActivityWeather`` (API).
    """
    if not raw or not isinstance(raw, dict):
        return {"available": False}
    station = raw.get("weatherStationDTO") or {}
    wtype = raw.get("weatherTypeDTO") or {}
    return {
        "available": True,
        "issue_date": raw.get("issueDate"),
        "temp_c": _f_to_c(raw.get("temp")),
        "apparent_temp_c": _f_to_c(raw.get("apparentTemp")),
        "dew_point_c": _f_to_c(raw.get("dewPoint")),
        "relative_humidity_pct": raw.get("relativeHumidity"),
        "wind_direction_deg": raw.get("windDirection"),
        "wind_compass": raw.get("windDirectionCompassPoint"),
        "description": wtype.get("desc") if isinstance(wtype, dict) else None,
        "station": station.get("id") if isinstance(station, dict) else None,
    }


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
                sport_type, avg_temp, min_temp, max_temp, map_polyline,
                name, calories, max_power, cadence_avg, cadence_max,
                speed_avg, speed_max, elevation_loss, start_lat, start_lng
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                activity.get("name"),
                activity.get("calories"),
                activity.get("max_power"),
                activity.get("cadence_avg"),
                activity.get("cadence_max"),
                activity.get("speed_avg"),
                activity.get("speed_max"),
                activity.get("elevation_loss"),
                activity.get("start_lat"),
                activity.get("start_lng"),
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
# Backfill one-off des champs enrichis (lignes Garmin antérieures au déploiement)
# ---------------------------------------------------------------------------

# Flag sync_meta posé quand le backfill a tourné avec succès — déclenchement
# automatique au premier sync suivant le déploiement, sinon retry au sync
# suivant en cas d'échec.
BACKFILL_FLAG = "garmin_fields_backfill_done"

_BACKFILL_COLUMNS = (
    "name",
    "calories",
    "max_power",
    "cadence_avg",
    "cadence_max",
    "speed_avg",
    "speed_max",
    "elevation_loss",
    "start_lat",
    "start_lng",
)

# Distance minimale pour justifier un appel détails (tracé GPS) au backfill.
_BACKFILL_MIN_DISTANCE_M = 1000.0

_MAX_POLYLINE_POINTS = 300


def backfill_garmin_fields(
    client: Any | None = None,
    db_path: Path | None = None,
    *,
    ctx: AthleteContext | None = None,
) -> dict[str, int]:
    """Complète les lignes Garmin existantes avec les champs enrichis.

    One-off (flag ``sync_meta`` posé par l'appelant en cas de succès) :
    re-fetch la liste Garmin sur la fenêtre couverte par les lignes garmin en
    base, puis ``UPDATE`` des nouveaux champs (name, calories, cadence,
    vitesse, D−, point de départ). Pour les activités avec GPS, un appel
    ``get_activity_details`` récupère en plus — au même coût réseau — le tracé
    GPS (``geoPolylineDTO`` → ``map_polyline`` encodée) et, pour les lignes
    encore à ``NULL``, les zones HR + température depuis les streams (rattrape
    les activités dont le parsing des détails échouait avant la correction du
    parser 09/2026).

    Retourne ``{"updated": n, "polylines": n, "details": n}``.
    """
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    init_db(path, ctx=ctx)
    if client is None:
        client = get_ingest_client()

    conn = sqlite3.connect(path)
    try:
        bounds = conn.execute(
            "SELECT MIN(date), MAX(date) FROM activities WHERE garmin_id IS NOT NULL"
        ).fetchone()
        rows = conn.execute(
            "SELECT garmin_id, avg_heart_rate, hr_z1_time, hr_z2_time, hr_z3_time, "
            "hr_z4_time, hr_z5_time, avg_temp, min_temp, max_temp, map_polyline "
            "FROM activities WHERE garmin_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not bounds or bounds[0] is None:
        return {"updated": 0, "polylines": 0, "details": 0}
    start = (_parse_gmt(bounds[0]) or dt.datetime.now(dt.UTC)).date() - dt.timedelta(days=1)
    end = (_parse_gmt(bounds[1]) or dt.date.today()).date() + dt.timedelta(days=1)

    # État par ligne : zones/temp à NULL → rattrapage possible via les streams.
    existing: dict[int, dict[str, Any]] = {}
    for r in rows:
        existing[int(r[0])] = {
            "avg_hr": r[1],
            "zones": (r[2], r[3], r[4], r[5], r[6]),
            "temp": (r[7], r[8], r[9]),
            "polyline": r[10],
        }

    try:
        raws = client.get_activities_by_date(start.isoformat(), end.isoformat())
    except Exception as exc:  # noqa: BLE001 — remap avec contexte
        raise GarminIngestError(f"Lecture des activités Garmin échouée : {exc}") from exc

    hr_rest = ctx.hr_rest if ctx else get_hr_rest()
    hr_max = ctx.hr_max if ctx else get_hr_max()
    zone_params: tuple[float, float] | None = (
        (float(hr_rest), float(hr_max)) if hr_rest and hr_max and hr_max > hr_rest else None
    )

    updated = polylines = details_calls = 0
    assignments: list[tuple[Any, ...]] = []
    polyline_updates: list[tuple[Any, ...]] = []
    zone_temp_updates: list[tuple[Any, ...]] = []

    for raw in raws:
        data = extract_activity_data(raw)
        if data is None:
            continue
        garmin_id = data["id"]
        state = existing.get(garmin_id)
        if state is None:
            continue  # pas (encore) en base — la sync s'en charge

        assignments.append((*[data[col] for col in _BACKFILL_COLUMNS], garmin_id))
        updated += 1

        need_polyline = (
            raw.get("hasPolyline")
            and (data.get("distance") or 0) >= _BACKFILL_MIN_DISTANCE_M
            and not state["polyline"]
        )
        zones_missing = bool(
            zone_params and state["avg_hr"] and all(v is None for v in state["zones"])
        )
        temp_missing = bool(state["avg_hr"] and all(v is None for v in state["temp"]))
        if not (need_polyline or zones_missing or temp_missing):
            continue

        try:
            details = client.get_activity_details(str(garmin_id))
        except Exception:  # noqa: BLE001 — une activité KO n'arrête pas le backfill
            log.warning("Backfill Garmin %s : détails indisponibles.", garmin_id, exc_info=True)
            continue
        details_calls += 1

        if need_polyline:
            points = _downsample_points(_polyline_from_details(details), _MAX_POLYLINE_POINTS)
            encoded = encode_polyline(points)
            if encoded:
                polyline_updates.append((encoded, garmin_id))
                polylines += 1

        if zones_missing or temp_missing:
            streams = parse_details_streams(details)
            zones = (
                calculate_hr_zones(streams["heartrate"], streams["time"], *zone_params)
                if zones_missing and streams and streams.get("heartrate") and streams.get("time")
                else None
            )
            temp_summary = (
                summarize_temp_stream(streams.get("temp")) if temp_missing and streams else None
            )
            if zones or temp_summary:
                zone_values = [zones.get(key) for key in HR_ZONE_KEYS] if zones else [None] * 5
                temp_values = list(temp_summary) if temp_summary else [None, None, None]
                zone_temp_updates.append((*zone_values, *temp_values, garmin_id))

    conn = sqlite3.connect(path)
    try:
        if assignments:
            conn.executemany(
                f"UPDATE activities SET {', '.join(f'{col} = ?' for col in _BACKFILL_COLUMNS)} "
                "WHERE garmin_id = ?",
                assignments,
            )
        if polyline_updates:
            conn.executemany(
                "UPDATE activities SET map_polyline = ? WHERE garmin_id = ?",
                polyline_updates,
            )
        if zone_temp_updates:
            conn.executemany(
                "UPDATE activities SET "
                "hr_z1_time = ?, hr_z2_time = ?, hr_z3_time = ?, hr_z4_time = ?, hr_z5_time = ?, "
                "avg_temp = ?, min_temp = ?, max_temp = ? WHERE garmin_id = ?",
                zone_temp_updates,
            )
        conn.commit()
    finally:
        conn.close()

    log.info(
        "Backfill Garmin %s → %s : %d lignes enrichies, %d tracés, %d appels détails.",
        start,
        end,
        updated,
        polylines,
        details_calls,
    )
    return {"updated": updated, "polylines": polylines, "details": details_calls}


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

    # Backfill one-off des champs enrichis sur les lignes antérieures au
    # déploiement (flag sync_meta). Best-effort : jamais bloquant pour la sync,
    # retry au sync suivant en cas d'échec (flag posé seulement en cas de succès).
    path = Path(db_path) if db_path else (ctx.db_path if ctx else get_db_path())
    if get_sync_meta(BACKFILL_FLAG, path) is None:
        try:
            backfill_garmin_fields(client, path, ctx=ctx)
            set_sync_meta(BACKFILL_FLAG, dt.date.today().isoformat(), path)
        except Exception:  # noqa: BLE001 — le backfill ne doit jamais casser la sync
            log.warning(
                "Backfill des champs Garmin échoué — retenté au prochain sync.",
                exc_info=True,
            )

    return inserted
