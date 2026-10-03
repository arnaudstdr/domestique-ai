"""
Recherche d'activités « similaires » à une activité donnée.

Une activité est jugée similaire quand elle relève du **même bucket de sport**
(indoor / outdoor / autre), que ses **distance** et **dénivelé** sont à moins de
quelques pourcents de ceux de l'activité de référence, puis — quand la donnée
GPS est disponible des deux côtés — que son **point de départ** est proche
(≤ 500 m) et que son **tracé** est quasi identique (Fréchet discret ≤ 500 m).

Cette heuristique retrouve une boucle hebdomadaire ou un col répété sans
dépendre d'une API distante. Les critères GPS sont appliqués « si disponible »
(hard filter seulement quand les deux activités portent la donnée) : une
activité sans GPS (manuel, Strava legacy, sortie sans HR donc sans polyline)
retombe sur la comparaison distance + dénivelé.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path
from domestique_ai.ingestion.db import init_db
from domestique_ai.processing.activity_classify import sport_bucket
from domestique_ai.processing.geo import (
    decode_polyline,
    discrete_frechet_m,
    haversine_m,
    resample_polyline,
)

# Tolérances : ±5 % sur la distance, ±10 % sur le dénivelé. Distance est plus
# fiable que dénivelé (GPS s'éclate sur le dénivelé lors d'arbres, tunnels).
_DISTANCE_TOLERANCE = 0.05
_ELEVATION_TOLERANCE = 0.10

# Plancher en mètres pour les très courtes activités : sous ces seuils, les
# comparaisons relatives n'ont pas de sens (5 % de 2 km = 100 m, ridicule).
_DISTANCE_FLOOR_M = 5_000.0
_ELEVATION_FLOOR_M = 50.0

# Critères GPS (appliqués uniquement quand la donnée existe des deux côtés).
# Le seuil de départ écarte les sorties qui ne partent pas du même lieu, le
# seuil de tracé (Fréchet) écarte deux boucles au même profil mais à la forme
# différente. Rééchantillonnage borné pour un coût de comparaison maîtrisé.
_START_PROXIMITY_M = 500.0
_TRACK_TOLERANCE_M = 500.0
_TRACK_RESAMPLE_POINTS = 64

# Buckets de sport (indoor/outdoor/other) : voir processing/activity_classify.py
# — on ne compare jamais une sortie route à un home trainer.


def _within_tolerance(a: float, b: float, tolerance: float, floor: float) -> bool:
    """``True`` si ``a`` et ``b`` sont à ``tolerance`` près en relatif.

    Le ``floor`` évite que des activités très courtes (où l'écart relatif n'a
    pas de sens) ne matchent ou n'écartent trop facilement.
    """
    if a is None or b is None:
        return False
    if a <= 0 or b <= 0:
        return a == b
    reference = max(a, b, floor)
    return abs(a - b) / reference <= tolerance


def _activity_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    # Id externe : strava_id (legacy), fallback garmin_id, puis id local
    # (activités manuelles / importées TCX).
    for index in (0, 1, 10):
        if row[index] is not None:
            external_id = row[index]
            break
    else:
        external_id = None
    return {
        "external_id": external_id,
        "date": row[2],
        # `duration` peut être stockée en REAL sur les lignes Garmin héritées
        # (valeur brute du payload). On caste défensivement en secondes entières.
        "duration_sec": int(row[3]) if row[3] is not None else None,
        "avg_heart_rate": row[4],
        "avg_power": row[5],
        "elevation_gain": row[6],
        "distance": row[7],
        "training_load": row[8],
        "sport_type": row[9],
        "start_lat": row[11] if len(row) > 11 else None,
        "start_lng": row[12] if len(row) > 12 else None,
        "map_polyline": row[13] if len(row) > 13 else None,
    }


def _track_points(encoded: str | None) -> list[tuple[float, float]] | None:
    """Décode + rééchantillonne un tracé encodé, ou ``None`` s'il est inexploitable."""
    if not encoded:
        return None
    points = decode_polyline(encoded)
    if len(points) < 2:
        return None
    return resample_polyline(points, _TRACK_RESAMPLE_POINTS)


def find_similar_activities(
    external_id: int,
    *,
    limit: int = 20,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Retourne les activités similaires à ``external_id``, triées date desc.

    Args:
        external_id : identifiant externe de l'activité de référence
            (strava_id historique ou garmin_id).
        limit : nombre maximum d'activités similaires retournées (≥ 1).
        db_path : chemin DB optionnel (utile pour les tests).

    Returns:
        Un dict :

        - ``"available": False`` + ``"reason"`` si l'activité de référence est
          introuvable ou si elle n'a ni distance ni dénivelé exploitables.
        - Sinon ``"available": True``, ``"reference": {...}``,
          ``"matches": [...]`` (potentiellement vide) et
          ``"criteria"`` (tolérances appliquées, utile pour le debug).
    """
    limit = max(1, min(int(limit), 100))
    path = Path(db_path) if db_path else get_db_path()
    init_db(path)

    conn = sqlite3.connect(path)
    try:
        # Index utile pour borner le scan sur la fenêtre [d-5%, d+5%].
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activities_distance_elev "
            "ON activities(distance, elevation_gain)"
        )
        ref_row = conn.execute(
            "SELECT strava_id, garmin_id, date, duration, avg_heart_rate, avg_power, "
            "elevation_gain, distance, training_load, sport_type, id, "
            "start_lat, start_lng, map_polyline "
            "FROM activities WHERE strava_id = ? OR garmin_id = ? OR id = ?",
            (external_id, external_id, external_id),
        ).fetchone()
        if ref_row is None:
            return {
                "available": False,
                "reason": f"Activité {external_id} introuvable en base.",
            }
        reference = _activity_to_dict(ref_row)
        if not reference["distance"] or reference["distance"] < _DISTANCE_FLOOR_M:
            return {
                "available": False,
                "reason": (
                    "Activité trop courte ou sans distance — comparaison non "
                    "pertinente (plancher : "
                    f"{int(_DISTANCE_FLOOR_M / 1000)} km)."
                ),
            }

        ref_bucket = sport_bucket(reference["sport_type"])
        ref_dist = float(reference["distance"])
        ref_elev = float(reference["elevation_gain"] or 0)
        ref_start = (
            (float(reference["start_lat"]), float(reference["start_lng"]))
            if reference["start_lat"] is not None and reference["start_lng"] is not None
            else None
        )
        ref_track = _track_points(reference["map_polyline"])

        # Pré-filtre SQL grossier : on garde toutes les activités dans la
        # fenêtre élargie de ±10 % de distance. Le filtre fin (tolérance + sport
        # + dénivelé) est appliqué côté Python. Sur la DB courante (~quelques
        # milliers de lignes) c'est tout à fait acceptable, et ça nous évite de
        # gérer les buckets sport en SQL.
        dist_lo = ref_dist * (1 - _DISTANCE_TOLERANCE * 2)
        dist_hi = ref_dist * (1 + _DISTANCE_TOLERANCE * 2)
        rows = conn.execute(
            "SELECT strava_id, garmin_id, date, duration, avg_heart_rate, avg_power, "
            "elevation_gain, distance, training_load, sport_type, id, "
            "start_lat, start_lng, map_polyline "
            "FROM activities "
            "WHERE coalesce(strava_id, garmin_id, id) != ? AND distance BETWEEN ? AND ? "
            "ORDER BY date DESC",
            (external_id, dist_lo, dist_hi),
        ).fetchall()
    finally:
        conn.close()

    matches: list[dict[str, Any]] = []
    for row in rows:
        candidate = _activity_to_dict(row)
        if sport_bucket(candidate["sport_type"]) != ref_bucket:
            continue
        if not _within_tolerance(
            ref_dist,
            candidate["distance"] or 0,
            _DISTANCE_TOLERANCE,
            _DISTANCE_FLOOR_M,
        ):
            continue
        cand_elev = float(candidate["elevation_gain"] or 0)
        if not _within_tolerance(
            ref_elev,
            cand_elev,
            _ELEVATION_TOLERANCE,
            _ELEVATION_FLOOR_M,
        ):
            continue

        # Proximité du départ : hard filter seulement si les deux ont un GPS
        # (sinon on ignore le critère et on retombe sur distance + dénivelé).
        start_distance_m: float | None = None
        if (
            ref_start is not None
            and candidate["start_lat"] is not None
            and candidate["start_lng"] is not None
        ):
            start_distance_m = haversine_m(
                ref_start[0],
                ref_start[1],
                float(candidate["start_lat"]),
                float(candidate["start_lng"]),
            )
            if start_distance_m > _START_PROXIMITY_M:
                continue

        # Forme du tracé : hard filter si les deux ont une polyline exploitable.
        track_distance_m: float | None = None
        if ref_track is not None:
            candidate_track = _track_points(candidate["map_polyline"])
            if candidate_track is not None:
                track_distance_m = discrete_frechet_m(ref_track, candidate_track)
                if track_distance_m is None or track_distance_m > _TRACK_TOLERANCE_M:
                    continue

        matches.append(
            {
                "external_id": candidate["external_id"],
                "date": candidate["date"],
                "duration_sec": candidate["duration_sec"],
                "avg_heart_rate": candidate["avg_heart_rate"],
                "avg_power": candidate["avg_power"],
                "elevation_m": cand_elev,
                "distance_km": round((candidate["distance"] or 0) / 1000, 2),
                "training_load": candidate["training_load"],
                "start_distance_m": (
                    round(start_distance_m, 1) if start_distance_m is not None else None
                ),
                "track_distance_m": (
                    round(track_distance_m, 1) if track_distance_m is not None else None
                ),
                "duration_delta_pct": _safe_delta_pct(
                    reference["duration_sec"], candidate["duration_sec"]
                ),
                "tss_delta_pct": _safe_delta_pct(
                    reference["training_load"], candidate["training_load"]
                ),
                "power_delta_pct": _safe_delta_pct(reference["avg_power"], candidate["avg_power"]),
            }
        )
        if len(matches) >= limit:
            break

    return {
        "available": True,
        "reference": {
            "external_id": reference["external_id"],
            "date": reference["date"],
            "distance_km": round(ref_dist / 1000, 2),
            "elevation_m": ref_elev,
            "duration_sec": reference["duration_sec"],
            "training_load": reference["training_load"],
            "sport_bucket": ref_bucket,
            "has_gps": ref_start is not None,
            "has_track": ref_track is not None,
        },
        "matches": matches,
        "criteria": {
            "distance_tolerance_pct": _DISTANCE_TOLERANCE * 100,
            "elevation_tolerance_pct": _ELEVATION_TOLERANCE * 100,
            "sport_bucket": ref_bucket,
            "start_proximity_m": _START_PROXIMITY_M,
            "track_tolerance_m": _TRACK_TOLERANCE_M,
        },
    }


def _safe_delta_pct(reference: float | None, candidate: float | None) -> float | None:
    """Δ relatif en % de ``candidate`` par rapport à ``reference``.

    Positif = candidate plus grand que reference. ``None`` si une valeur
    manque ou si la référence est nulle (delta indéfini).
    """
    if reference is None or candidate is None:
        return None
    ref = float(reference)
    cand = float(candidate)
    if ref == 0:
        return None
    return round((cand - ref) / ref * 100, 1)


def parse_date(date_iso: str) -> _dt.date | None:
    """Helper exposé pour les tests : ``"2026-05-21T08:00:00Z"`` → ``date``."""
    try:
        return _dt.date.fromisoformat(date_iso[:10])
    except (ValueError, TypeError):
        return None


__all__ = ["find_similar_activities", "parse_date"]
