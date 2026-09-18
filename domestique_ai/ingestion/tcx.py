"""Parsing des fichiers TCX (Training Center XML) importés manuellement.

Le module ne dépend d'aucune source distante : il transforme des octets TCX en
agrégats prêts à persister (durée, distance, D+, FC, puissance, tracé) + un
dict de streams pour la page détail. Le calcul du TSS et des zones HR reste du
ressort du processing (``analyzer``) — on ne fait ici que de l'extraction.

Le TCX est du XML Garmin, avec des namespaces qui varient selon l'exportateur :
tout le parsing se fait donc par *local-name* (on ignore ``{namespace}tag``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from domestique_ai.ingestion.garmin import _downsample_points, encode_polyline

# Sport TCX → nomenclature Strava/Garmin historique (mêmes buckets indoor/outdoor
# que le reste de la pipeline). Repli : TitleCase du sport brut.
_TCX_SPORT_MAP = {
    "biking": "Ride",
    "running": "Run",
    "swimming": "Swim",
    "walking": "Walk",
    "hiking": "Hike",
    "other": "Workout",
}


def _local(tag: str) -> str:
    """Retire le namespace d'un tag XML (``{ns}Trackpoint`` → ``Trackpoint``)."""
    return tag.rsplit("}", 1)[-1]


def _iter_local(parent: ET.Element, name: str):
    """Itère les descendants dont le local-name vaut ``name``."""
    return (el for el in parent.iter() if _local(el.tag) == name)


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    for el in parent:
        if _local(el.tag) == name:
            return el
    return None


def _text(parent: ET.Element, name: str) -> str | None:
    el = _child(parent, name)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _float(parent: ET.Element, name: str) -> float | None:
    raw = _text(parent, name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_time(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def _map_sport(sport: str | None) -> str:
    if not sport:
        return "Workout"
    return _TCX_SPORT_MAP.get(sport.strip().lower(), sport.strip().title())


def _watts(trackpoint: ET.Element) -> float | None:
    """Cherche ``Watts`` dans les Extensions (namespace Garmin ActivityExtension)."""
    for el in trackpoint.iter():
        if _local(el.tag) == "Watts" and el.text:
            try:
                return float(el.text)
            except ValueError:
                return None
    return None


def _speed(trackpoint: ET.Element) -> float | None:
    for el in trackpoint.iter():
        if _local(el.tag) == "Speed" and el.text:
            try:
                return float(el.text)
            except ValueError:
                return None
    return None


@dataclass
class TcxActivity:
    """Activité extraite d'un ``<Activity>`` TCX."""

    date: str | None
    sport_type: str
    name: str | None
    duration: int
    distance: float | None
    elevation_gain: float | None
    elevation_loss: float | None
    avg_heart_rate: float | None
    max_heart_rate: float | None
    avg_power: float | None
    max_power: float | None
    cadence_avg: float | None
    cadence_max: float | None
    speed_avg: float | None
    speed_max: float | None
    calories: float | None
    start_lat: float | None
    start_lng: float | None
    map_polyline: str | None
    streams: dict[str, list[Any]] = field(default_factory=dict)
    # Séries HR/temps alignées (échantillons où les deux existent) — sert au
    # calcul des zones HR, qui exige des listes de même longueur.
    zone_time_stream: list[int] = field(default_factory=list)
    zone_hr_stream: list[float] = field(default_factory=list)


def _parse_activity(activity: ET.Element) -> TcxActivity | None:
    sport_type = _map_sport(activity.get("Sport"))
    name = _text(activity, "Notes")

    records: list[dict[str, Any]] = []
    for tp in _iter_local(activity, "Trackpoint"):
        when = _parse_time(_text(tp, "Time"))
        lat = lng = None
        position = _child(tp, "Position")
        if position is not None:
            lat = _float(position, "LatitudeDegrees")
            lng = _float(position, "LongitudeDegrees")
        # HeartRateBpm est un élément conteneur (<Value> à l'intérieur).
        hr_el = _child(tp, "HeartRateBpm")
        records.append(
            {
                "t": when,
                "hr": _float(hr_el, "Value") if hr_el is not None else None,
                "alt": _float(tp, "AltitudeMeters"),
                "dist": _float(tp, "DistanceMeters"),
                "lat": lat,
                "lng": lng,
                "cad": _float(tp, "Cadence"),
                "watts": _watts(tp),
                "speed": _speed(tp),
            }
        )

    # Lap totals (fallback + calories + durée agrégée).
    lap_duration = 0.0
    lap_distance = 0.0
    lap_calories = 0.0
    for lap in _iter_local(activity, "Lap"):
        lap_duration += _float(lap, "TotalTimeSeconds") or 0.0
        lap_distance += _float(lap, "DistanceMeters") or 0.0
        lap_calories += _float(lap, "Calories") or 0.0

    times = [r["t"] for r in records if r["t"] is not None]
    if not records or not times:
        return None

    t0 = min(times)
    for r in records:
        r["rel"] = (r["t"] - t0).total_seconds() if r["t"] is not None else None

    def series(key: str) -> list[float]:
        return [float(r[key]) for r in records if r.get(key) is not None]

    hr_series = series("hr")
    alt_series = series("alt")
    dist_series = series("dist")
    cad_series = series("cad")
    watts_series = series("watts")
    speed_series = series("speed")
    latlng = [
        [float(r["lat"]), float(r["lng"])]
        for r in records
        if r.get("lat") is not None and r.get("lng") is not None
    ]
    time_series = [int(round(r["rel"])) for r in records if r.get("rel") is not None]
    zone_pairs = [
        (int(round(r["rel"])), float(r["hr"]))
        for r in records
        if r.get("rel") is not None and r.get("hr") is not None
    ]

    # Durée : préférer le total des laps, sinon span du 1er au dernier point.
    duration = int(round(lap_duration)) if lap_duration > 0 else time_series[-1]
    # Distance : dernière valeur du stream, sinon somme des laps.
    if dist_series:
        distance = dist_series[-1]
    elif lap_distance > 0:
        distance = lap_distance
    else:
        distance = None

    elevation_gain = elevation_loss = None
    if len(alt_series) >= 2:
        gain = loss = 0.0
        for prev, cur in zip(alt_series, alt_series[1:], strict=False):
            delta = cur - prev
            if delta > 0:
                gain += delta
            else:
                loss -= delta
        elevation_gain = round(gain, 1)
        elevation_loss = round(loss, 1)

    # Vitesse : stream explicite TCX sinon dérivée de (distance, temps).
    if not speed_series and len(dist_series) >= 2 and len(time_series) >= 2:
        derived = []
        for i in range(1, min(len(dist_series), len(time_series))):
            dt_s = time_series[i] - time_series[i - 1]
            if dt_s > 0:
                derived.append((dist_series[i] - dist_series[i - 1]) / dt_s)
        speed_series = derived

    streams: dict[str, list[Any]] = {"time": time_series}
    if hr_series:
        streams["heartrate"] = hr_series
    if alt_series:
        streams["altitude"] = alt_series
    if watts_series:
        streams["watts"] = watts_series
    if cad_series:
        streams["cadence"] = cad_series
    if dist_series:
        streams["distance"] = dist_series
    if speed_series:
        streams["velocity_smooth"] = speed_series
    if latlng:
        streams["latlng"] = latlng

    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    start_lat = start_lng = None
    if latlng:
        start_lat, start_lng = latlng[0]

    return TcxActivity(
        date=t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        sport_type=sport_type,
        name=name,
        duration=duration,
        distance=round(distance, 1) if distance is not None else None,
        elevation_gain=elevation_gain,
        elevation_loss=elevation_loss,
        avg_heart_rate=avg(hr_series),
        max_heart_rate=round(max(hr_series), 1) if hr_series else None,
        avg_power=avg(watts_series),
        max_power=round(max(watts_series), 1) if watts_series else None,
        cadence_avg=avg(cad_series),
        cadence_max=round(max(cad_series), 1) if cad_series else None,
        speed_avg=avg(speed_series),
        speed_max=round(max(speed_series), 1) if speed_series else None,
        calories=round(lap_calories, 1) if lap_calories > 0 else None,
        start_lat=start_lat,
        start_lng=start_lng,
        map_polyline=encode_polyline(_downsample_points([(la, ln) for la, ln in latlng])),
        streams=streams,
        zone_time_stream=[t for t, _ in zone_pairs],
        zone_hr_stream=[hr for _, hr in zone_pairs],
    )


def parse_tcx(content: bytes) -> list[TcxActivity]:
    """Parse un fichier TCX en une liste d'activités (un fichier peut en contenir N).

    Lève ``TcxParseError`` si le XML est illisible ou ne contient aucun
    ``<Activity>`` exploitable.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise TcxParseError(f"XML invalide : {exc}") from exc

    activities: list[TcxActivity] = []
    for activity in _iter_local(root, "Activity"):
        parsed = _parse_activity(activity)
        if parsed is not None:
            activities.append(parsed)
    if not activities:
        raise TcxParseError("Aucune activité exploitable dans le fichier TCX.")
    return activities


class TcxParseError(ValueError):
    """Fichier TCX illisible ou vide."""


__all__ = ["TcxActivity", "TcxParseError", "parse_tcx"]
