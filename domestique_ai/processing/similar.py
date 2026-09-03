"""
Recherche d'activités « similaires » à une activité donnée.

Une activité est jugée similaire quand elle relève du **même bucket de sport**
(indoor / outdoor / autre) et que ses **distance** et **dénivelé** sont à
moins de quelques pourcents de ceux de l'activité de référence.

Cette heuristique simple (sans GPS de départ) suffit à retrouver une boucle
hebdomadaire qu'un cycliste répète, sans dépendre de l'API Strava ni d'un
calcul de proximité GPS coûteux. Si l'usage révèle trop de faux positifs (par
exemple deux sorties différentes au même profil), on ajoutera ``start_lat`` /
``start_lng`` lors d'une phase 2.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any

from domestique_ai.config import get_db_path

# Tolérances : ±5 % sur la distance, ±10 % sur le dénivelé. Distance est plus
# fiable que dénivelé (GPS s'éclate sur le dénivelé lors d'arbres, tunnels).
_DISTANCE_TOLERANCE = 0.05
_ELEVATION_TOLERANCE = 0.10

# Plancher en mètres pour les très courtes activités : sous ces seuils, les
# comparaisons relatives n'ont pas de sens (5 % de 2 km = 100 m, ridicule).
_DISTANCE_FLOOR_M = 5_000.0
_ELEVATION_FLOOR_M = 50.0

# Buckets de sport : on ne compare jamais une sortie route à un home trainer.
# ``None`` ou un sport non listé tombent dans le bucket ``"other"`` et ne
# matchent qu'eux-mêmes (rarement utile, mais on évite les faux positifs).
_INDOOR_SPORTS = {"VirtualRide"}
_OUTDOOR_SPORTS = {"Ride", "GravelRide", "MountainBikeRide", "EBikeRide"}


def _sport_bucket(sport_type: str | None) -> str:
    """Retourne ``"indoor"``, ``"outdoor"`` ou ``"other"`` selon le sport."""
    if sport_type in _INDOOR_SPORTS:
        return "indoor"
    if sport_type in _OUTDOOR_SPORTS:
        return "outdoor"
    return "other"


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
    # Id externe : strava_id prioritaire, fallback garmin_id (source Garmin).
    external_id = row[0] if row[0] is not None else row[1]
    return {
        "strava_id": external_id,
        "date": row[2],
        "duration_sec": row[3],
        "avg_heart_rate": row[4],
        "avg_power": row[5],
        "elevation_gain": row[6],
        "distance": row[7],
        "training_load": row[8],
        "sport_type": row[9],
    }


def find_similar_activities(
    strava_id: int,
    *,
    limit: int = 20,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Retourne les activités similaires à ``strava_id``, triées date desc.

    Args:
        strava_id : identifiant Strava de l'activité de référence.
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
    from domestique_ai.ingestion.strava import init_db

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
            "elevation_gain, distance, training_load, sport_type "
            "FROM activities WHERE strava_id = ? OR garmin_id = ?",
            (strava_id, strava_id),
        ).fetchone()
        if ref_row is None:
            return {
                "available": False,
                "reason": f"Activité {strava_id} introuvable en base.",
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

        ref_bucket = _sport_bucket(reference["sport_type"])
        ref_dist = float(reference["distance"])
        ref_elev = float(reference["elevation_gain"] or 0)

        # Pré-filtre SQL grossier : on garde toutes les activités dans la
        # fenêtre élargie de ±10 % de distance. Le filtre fin (tolérance + sport
        # + dénivelé) est appliqué côté Python. Sur la DB courante (~quelques
        # milliers de lignes) c'est tout à fait acceptable, et ça nous évite de
        # gérer les buckets sport en SQL.
        dist_lo = ref_dist * (1 - _DISTANCE_TOLERANCE * 2)
        dist_hi = ref_dist * (1 + _DISTANCE_TOLERANCE * 2)
        rows = conn.execute(
            "SELECT strava_id, garmin_id, date, duration, avg_heart_rate, avg_power, "
            "elevation_gain, distance, training_load, sport_type "
            "FROM activities "
            "WHERE coalesce(strava_id, garmin_id) != ? AND distance BETWEEN ? AND ? "
            "ORDER BY date DESC",
            (strava_id, dist_lo, dist_hi),
        ).fetchall()
    finally:
        conn.close()

    matches: list[dict[str, Any]] = []
    for row in rows:
        candidate = _activity_to_dict(row)
        if _sport_bucket(candidate["sport_type"]) != ref_bucket:
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
        matches.append(
            {
                "strava_id": candidate["strava_id"],
                "date": candidate["date"],
                "duration_sec": candidate["duration_sec"],
                "avg_heart_rate": candidate["avg_heart_rate"],
                "avg_power": candidate["avg_power"],
                "elevation_m": cand_elev,
                "distance_km": round((candidate["distance"] or 0) / 1000, 2),
                "training_load": candidate["training_load"],
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
            "strava_id": reference["strava_id"],
            "date": reference["date"],
            "distance_km": round(ref_dist / 1000, 2),
            "elevation_m": ref_elev,
            "duration_sec": reference["duration_sec"],
            "training_load": reference["training_load"],
            "sport_bucket": ref_bucket,
        },
        "matches": matches,
        "criteria": {
            "distance_tolerance_pct": _DISTANCE_TOLERANCE * 100,
            "elevation_tolerance_pct": _ELEVATION_TOLERANCE * 100,
            "sport_bucket": ref_bucket,
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
