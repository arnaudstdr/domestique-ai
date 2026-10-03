"""Détection, appariement et statistiques des montées (climbs).

Les montées sont détectées sur les **streams persistés** (``activity_streams`` :
tracés Garmin alignés issus du backfill ``ingestion/backfill_streams.py``,
streams TCX) — aucune donnée réseau ici. Pipeline :

1. ``detect_climbs`` (pur) : lissage d'altitude, pente par segment, runs en
   montée avec hystérésis, fusion des interruptions < 150 m, filtre final
   (pente moyenne ≥ 3 %, D+ ≥ 40 m, longueur ≥ 800 m).
2. ``rebuild_climbs`` : apparie les montées entre sorties (départ/arrivée
   proches ≤ 250 m + longueur ± 30 %), **préserve les noms** des segments
   existants, réécrit ``climb_efforts`` puis les compteurs.
3. ``climb_report`` / ``climb_detail`` / ``rename_climb`` : lecture pour le tool
   coach et l'API.

Un changement de seuils nécessite un ``rebuild_climbs`` (les efforts sont
recalculés ; les noms survivent).
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
from domestique_ai.processing.geo import haversine_m

DEFAULT_MIN_LENGTH_M = 800.0
DEFAULT_MIN_GAIN_M = 40.0
DEFAULT_MIN_AVG_GRADIENT = 3.0
#: Pente d'un segment considéré « en montée » (hystérésis sous le seuil moyen).
_SEGMENT_MIN_GRADIENT = 2.0
#: Interruptions (virage, faux plat) fusionnées tant qu'elles durent moins que ça.
_MAX_GAP_M = 150.0
_ALTITUDE_WINDOW = 5
_MATCH_START_M = 250.0
_MATCH_END_M = 250.0
_MATCH_LENGTH_RATIO = (0.7, 1.3)


def _resolve_path(db_path: Path | str | None, ctx: AthleteContext | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if ctx is not None:
        return Path(ctx.db_path)
    return Path(get_db_path())


def _clean_track(payload: dict[str, Any]) -> list[dict[str, float | None]]:
    """Points alignés ``(t, distance, altitude, lat, lng, hr, power)`` sans trous.

    Les séries persistées sont index-aligned ; on saute les échantillons dont
    temps/distance/altitude manquent (un trou GPS n'invalide pas la montée).
    """
    time = payload.get("time") or []
    distance = payload.get("distance") or []
    altitude = payload.get("altitude") or []
    count = min(len(time), len(distance), len(altitude))
    lat = payload.get("lat") or []
    lng = payload.get("lng") or []
    hr = payload.get("heartrate") or []
    power = payload.get("power") or []

    def opt(series: list[Any], index: int) -> float | None:
        if index < len(series) and series[index] is not None:
            return float(series[index])
        return None

    points: list[dict[str, float | None]] = []
    for i in range(count):
        if time[i] is None or distance[i] is None or altitude[i] is None:
            continue
        points.append(
            {
                "t": float(time[i]),
                "d": float(distance[i]),
                "alt": float(altitude[i]),
                "lat": opt(lat, i),
                "lng": opt(lng, i),
                "hr": opt(hr, i),
                "power": opt(power, i),
            }
        )
    return points


def _smooth_altitudes(
    points: list[dict[str, float | None]], window: int = _ALTITUDE_WINDOW
) -> list[float]:
    """Moyenne glissante centrée sur l'altitude (bruit barométrique/GPS)."""
    altitudes = [float(point["alt"]) for point in points]
    half = max(1, window // 2)
    smoothed = []
    for i in range(len(altitudes)):
        lo = max(0, i - half)
        hi = min(len(altitudes), i + half + 1)
        values = altitudes[lo:hi]
        smoothed.append(sum(values) / len(values))
    return smoothed


def _build_climb(
    points: list[dict[str, float | None]],
    smoothed: list[float],
    start_idx: int,
    last_idx: int,
    min_length_m: float,
    min_gain_m: float,
    min_avg_gradient: float,
) -> dict[str, Any] | None:
    """Construit la montée ``[start_idx ; last_idx + 1]`` ou ``None`` si hors seuils."""
    start, end = points[start_idx], points[last_idx + 1]
    length = end["d"] - start["d"]
    gain = smoothed[last_idx + 1] - smoothed[start_idx]
    duration = end["t"] - start["t"]
    if length < min_length_m or gain < min_gain_m or duration <= 0:
        return None
    avg_gradient = gain / length * 100
    if avg_gradient < min_avg_gradient:
        return None

    grades = []
    for i in range(start_idx, last_idx + 1):
        delta_d = points[i + 1]["d"] - points[i]["d"]
        if delta_d > 0:
            grades.append((smoothed[i + 1] - smoothed[i]) / delta_d * 100)

    hr_values = [point["hr"] for point in points[start_idx : last_idx + 2] if point["hr"]]
    power_values = [point["power"] for point in points[start_idx : last_idx + 2] if point["power"]]
    return {
        "start_idx": start_idx,
        "end_idx": last_idx + 1,
        "length_m": round(length, 1),
        "gain_m": round(gain, 1),
        "avg_gradient_pct": round(avg_gradient, 2),
        "max_gradient_pct": round(max(grades), 2) if grades else None,
        "duration_sec": round(duration, 1),
        "vam_m_h": round(gain / duration * 3600, 1),
        "avg_hr": round(sum(hr_values) / len(hr_values), 1) if hr_values else None,
        "avg_power": round(sum(power_values) / len(power_values), 1) if power_values else None,
        "start_lat": start["lat"],
        "start_lng": start["lng"],
        "end_lat": end["lat"],
        "end_lng": end["lng"],
    }


def detect_climbs(
    payload: dict[str, Any],
    *,
    min_length_m: float = DEFAULT_MIN_LENGTH_M,
    min_gain_m: float = DEFAULT_MIN_GAIN_M,
    min_avg_gradient: float = DEFAULT_MIN_AVG_GRADIENT,
) -> list[dict[str, Any]]:
    """Détecte les montées d'un tracé persisté (streams alignés)."""
    points = _clean_track(payload)
    if len(points) < 10:
        return []
    smoothed = _smooth_altitudes(points)

    runs: list[tuple[int, int]] = []
    start_idx: int | None = None
    last_idx: int | None = None
    for i in range(len(points) - 1):
        delta_d = points[i + 1]["d"] - points[i]["d"]
        grade = (smoothed[i + 1] - smoothed[i]) / delta_d * 100 if delta_d > 0 else None
        climbing = grade is not None and grade >= _SEGMENT_MIN_GRADIENT
        if climbing:
            if start_idx is None:
                start_idx = i
            last_idx = i
        elif start_idx is not None and last_idx is not None:
            gap_m = points[i + 1]["d"] - points[last_idx + 1]["d"]
            if gap_m > _MAX_GAP_M:
                runs.append((start_idx, last_idx))
                start_idx = None
                last_idx = None
    if start_idx is not None and last_idx is not None:
        runs.append((start_idx, last_idx))

    climbs = []
    for run_start, run_end in runs:
        climb = _build_climb(
            points,
            smoothed,
            run_start,
            run_end,
            min_length_m,
            min_gain_m,
            min_avg_gradient,
        )
        if climb is not None:
            climbs.append(climb)
    return climbs


# ---------------------------------------------------------------------------
# Appariement + persistance
# ---------------------------------------------------------------------------


def _load_segments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, start_lat, start_lng, end_lat, end_lng, length_m, gain_m "
        "FROM climb_segments"
    ).fetchall()
    return [
        {
            "id": int(row[0]),
            "name": row[1],
            "start_lat": row[2],
            "start_lng": row[3],
            "end_lat": row[4],
            "end_lng": row[5],
            "length_m": row[6],
            "gain_m": row[7],
        }
        for row in rows
    ]


def _match_segment(segments: list[dict[str, Any]], climb: dict[str, Any]) -> dict[str, Any] | None:
    """Segment existant le plus proche (départ/arrivée ≤ 250 m, longueur ± 30 %)."""
    if climb["start_lat"] is None or climb["end_lat"] is None:
        return None
    best: dict[str, Any] | None = None
    best_score: float | None = None
    for segment in segments:
        if segment["start_lat"] is None or segment["end_lat"] is None:
            continue
        start_distance = haversine_m(
            climb["start_lat"], climb["start_lng"], segment["start_lat"], segment["start_lng"]
        )
        end_distance = haversine_m(
            climb["end_lat"], climb["end_lng"], segment["end_lat"], segment["end_lng"]
        )
        if start_distance > _MATCH_START_M or end_distance > _MATCH_END_M:
            continue
        ratio = climb["length_m"] / segment["length_m"] if segment["length_m"] else 0.0
        if not (_MATCH_LENGTH_RATIO[0] <= ratio <= _MATCH_LENGTH_RATIO[1]):
            continue
        score = start_distance + end_distance + abs(1 - ratio) * 100
        if best_score is None or score < best_score:
            best, best_score = segment, score
    return best


def rebuild_climbs(
    *,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, int]:
    """Reconstruit ``climb_efforts`` depuis les streams persistés (idempotent).

    Les segments existants sont conservés (noms compris) et ré-appariés ; les
    nouveaux tracés créent des segments sans nom. Retourne un résumé.
    """
    path = _resolve_path(db_path, ctx)
    init_db(path)
    conn = sqlite3.connect(path)
    created_at = dt.datetime.now(dt.UTC).isoformat()
    result = {
        "activities_with_streams": 0,
        "activities_with_climbs": 0,
        "segments_created": 0,
        "efforts_written": 0,
    }
    try:
        segments = _load_segments(conn)
        conn.execute("DELETE FROM climb_efforts")
        rows = conn.execute(
            "SELECT a.id, a.date, s.payload FROM activities a "
            "JOIN activity_streams s ON s.activity_id = a.id ORDER BY a.date ASC"
        ).fetchall()
        result["activities_with_streams"] = len(rows)
        for activity_id, date, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            climbs = detect_climbs(payload)
            if climbs:
                result["activities_with_climbs"] += 1
            for climb in climbs:
                segment = _match_segment(segments, climb)
                if segment is None:
                    cursor = conn.execute(
                        "INSERT INTO climb_segments (name, start_lat, start_lng, end_lat, "
                        "end_lng, length_m, gain_m, avg_gradient_pct, first_seen, last_seen, "
                        "created_at) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            climb["start_lat"],
                            climb["start_lng"],
                            climb["end_lat"],
                            climb["end_lng"],
                            climb["length_m"],
                            climb["gain_m"],
                            climb["avg_gradient_pct"],
                            date,
                            date,
                            created_at,
                        ),
                    )
                    segment = {
                        "id": int(cursor.lastrowid),
                        "name": None,
                        "start_lat": climb["start_lat"],
                        "start_lng": climb["start_lng"],
                        "end_lat": climb["end_lat"],
                        "end_lng": climb["end_lng"],
                        "length_m": climb["length_m"],
                        "gain_m": climb["gain_m"],
                    }
                    segments.append(segment)
                    result["segments_created"] += 1
                conn.execute(
                    "INSERT INTO climb_efforts (segment_id, activity_id, date, duration_sec, "
                    "vam_m_h, avg_hr, avg_power, avg_gradient_pct, max_gradient_pct) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        segment["id"],
                        int(activity_id),
                        date,
                        climb["duration_sec"],
                        climb["vam_m_h"],
                        climb["avg_hr"],
                        climb["avg_power"],
                        climb["avg_gradient_pct"],
                        climb["max_gradient_pct"],
                    ),
                )
                result["efforts_written"] += 1
        conn.execute(
            "UPDATE climb_segments SET "
            "efforts_count = (SELECT COUNT(*) FROM climb_efforts e WHERE e.segment_id = climb_segments.id), "
            "first_seen = (SELECT MIN(date) FROM climb_efforts e WHERE e.segment_id = climb_segments.id), "
            "last_seen = (SELECT MAX(date) FROM climb_efforts e WHERE e.segment_id = climb_segments.id)"
        )
        conn.commit()
    finally:
        conn.close()
    return result


# ---------------------------------------------------------------------------
# Lecture (tool coach / API)
# ---------------------------------------------------------------------------


def _segment_stats(conn: sqlite3.Connection, segment_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*), MIN(duration_sec), AVG(duration_sec), MAX(vam_m_h), "
        "MIN(date), MAX(date) FROM climb_efforts WHERE segment_id = ?",
        (segment_id,),
    ).fetchone()
    years = conn.execute(
        "SELECT substr(date, 1, 4), COUNT(*), MIN(duration_sec) FROM climb_efforts "
        "WHERE segment_id = ? GROUP BY 1 ORDER BY 1",
        (segment_id,),
    ).fetchall()
    return {
        "efforts": int(row[0]),
        "best_sec": round(row[1], 1) if row[1] is not None else None,
        "avg_sec": round(row[2], 1) if row[2] is not None else None,
        "best_vam_m_h": round(row[3], 1) if row[3] is not None else None,
        "first_date": row[4],
        "last_date": row[5],
        "by_year": [
            {"year": str(year), "efforts": int(count), "best_sec": round(best, 1)}
            for year, count, best in years
        ],
    }


def _segment_dict(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "name": row[1],
        "length_m": row[2],
        "gain_m": row[3],
        "avg_gradient_pct": row[4],
        "efforts_count": int(row[5]),
        "start_lat": row[6],
        "start_lng": row[7],
    }


_SEGMENT_COLUMNS = (
    "id, name, length_m, gain_m, avg_gradient_pct, efforts_count, start_lat, start_lng"
)


def climb_report(
    *,
    name: str | None = None,
    limit: int = 10,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any]:
    """Statistiques des montées pour le coach : passages, meilleur/moyen temps, VAM."""
    path = _resolve_path(db_path, ctx)
    if not Path(path).exists():
        return {"available": False, "reason": "Base athlète introuvable."}
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        total_segments = conn.execute("SELECT COUNT(*) FROM climb_segments").fetchone()[0]
        unnamed = conn.execute(
            "SELECT COUNT(*) FROM climb_segments WHERE (name IS NULL OR name = '') "
            "AND efforts_count > 0"
        ).fetchone()[0]
        sql = f"SELECT {_SEGMENT_COLUMNS} FROM climb_segments"
        params: list[Any] = []
        if name:
            sql += " WHERE name LIKE ?"
            params.append(f"%{name}%")
        sql += " ORDER BY efforts_count DESC, id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 50)))
        rows = conn.execute(sql, params).fetchall()

        if not rows:
            if total_segments == 0:
                return {
                    "available": False,
                    "reason": "Aucune montée détectée pour l'instant (streams Garmin non "
                    "backfillés ou historique sans GPS/altitude).",
                }
            return {
                "available": False,
                "reason": f"Aucune montée ne correspond à « {name} ». "
                f"{unnamed} montée(s) détectée(s) sans nom.",
                "unnamed_segments": int(unnamed),
            }

        segments = []
        for row in rows:
            segment = _segment_dict(row)
            segment.update(_segment_stats(conn, segment["id"]))
            segments.append(segment)
    finally:
        conn.close()
    return {
        "available": True,
        "segments": segments,
        "segments_count": len(segments),
        "total_segments": int(total_segments),
        "unnamed_segments": int(unnamed),
    }


def climb_detail(
    segment_id: int,
    *,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any] | None:
    """Détail d'un segment : stats + liste des efforts (activités)."""
    path = _resolve_path(db_path, ctx)
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            f"SELECT {_SEGMENT_COLUMNS} FROM climb_segments WHERE id = ?",
            (int(segment_id),),
        ).fetchone()
        if row is None:
            return None
        segment = _segment_dict(row)
        segment.update(_segment_stats(conn, segment["id"]))
        efforts = conn.execute(
            "SELECT e.date, e.duration_sec, e.vam_m_h, e.avg_hr, e.avg_power, "
            "e.avg_gradient_pct, e.max_gradient_pct, a.name, a.sport_type "
            "FROM climb_efforts e LEFT JOIN activities a ON a.id = e.activity_id "
            "WHERE e.segment_id = ? ORDER BY e.date DESC",
            (segment["id"],),
        ).fetchall()
        segment["efforts_list"] = [
            {
                "date": effort[0],
                "duration_sec": effort[1],
                "vam_m_h": effort[2],
                "avg_hr": effort[3],
                "avg_power": effort[4],
                "avg_gradient_pct": effort[5],
                "max_gradient_pct": effort[6],
                "activity_name": effort[7],
                "sport_type": effort[8],
            }
            for effort in efforts
        ]
        return segment
    finally:
        conn.close()


def list_climbs(
    *,
    limit: int = 100,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> list[dict[str, Any]]:
    """Liste des segments avec stats (pour la page « Montées »)."""
    path = _resolve_path(db_path, ctx)
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            f"SELECT {_SEGMENT_COLUMNS} FROM climb_segments "
            "ORDER BY efforts_count DESC, id ASC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        segments = []
        for row in rows:
            segment = _segment_dict(row)
            segment.update(_segment_stats(conn, segment["id"]))
            segments.append(segment)
        return segments
    finally:
        conn.close()


def rename_climb(
    segment_id: int,
    name: str,
    *,
    db_path: Path | str | None = None,
    ctx: AthleteContext | None = None,
) -> dict[str, Any] | None:
    """Nomme (ou renomme) un segment. ``name`` vide/``None`` efface le nom."""
    path = _resolve_path(db_path, ctx)
    init_db(path)
    cleaned = (name or "").strip() or None
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            "UPDATE climb_segments SET name = ? WHERE id = ?",
            (cleaned, int(segment_id)),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    finally:
        conn.close()
    return climb_detail(segment_id, db_path=path)
