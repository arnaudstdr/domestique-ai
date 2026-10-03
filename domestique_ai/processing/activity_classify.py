"""Classification pure des activités : type de séance (kind) et indoor/outdoor.

Module partagé, sans DB ni dépendance aval : `today.py` (dernière séance),
`similar.py` (comparateur), `compliance.py` (match plan/réalisé) et
`activity_stats.py` (mix par type) l'utilisent.
"""

from __future__ import annotations

_INDOOR_SPORTS = {"VirtualRide"}
_OUTDOOR_SPORTS = {"Ride", "GravelRide", "MountainBikeRide", "EBikeRide"}


def sport_bucket(sport_type: str | None) -> str:
    """Retourne ``"indoor"``, ``"outdoor"`` ou ``"other"`` selon le sport.

    Un sport non listé tombe dans ``"other"`` et ne matche que lui-même (on
    évite les faux positifs du comparateur).
    """
    if sport_type in _INDOOR_SPORTS:
        return "indoor"
    if sport_type in _OUTDOOR_SPORTS:
        return "outdoor"
    return "other"


def infer_kind_from_zones(
    z_times: dict[str, float | None],
    avg_hr: float | None,
) -> str | None:
    """Déduit le kind dominant d'une séance à partir de ses zones HR.

    Retourne None si les zones sont absentes (séance non backfillée).
    """
    if not z_times or all(v in (None, 0) for v in z_times.values()):
        return None
    total = sum(v or 0.0 for v in z_times.values())
    if total <= 0:
        return None
    shares = {k: (v or 0.0) / total for k, v in z_times.items()}
    if shares.get("z5", 0) + shares.get("z4", 0) >= 0.20:
        return "intervals"
    if shares.get("z3", 0) >= 0.30:
        return "tempo"
    if shares.get("z1", 0) >= 0.70 and (avg_hr is None or avg_hr < 130):
        return "recovery"
    return "endurance"
