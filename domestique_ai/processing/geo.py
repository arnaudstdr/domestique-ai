"""
Utilitaires géométriques purs (sans dépendance) pour le comparateur d'activités.

Utilisés par ``processing/similar.py`` pour rapprocher deux sorties par leur
point de départ (:func:`haversine_m`) et par la forme de leur tracé
(:func:`decode_polyline` + :func:`resample_polyline` + :func:`discrete_frechet_m`).
Aucune dépendance externe, aucune lecture DB — testable isolément.
"""

from __future__ import annotations

import math

# Rayon terrestre moyen (m).
_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance orthodromique en mètres entre deux points (lat, lng) en degrés."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Décode une polyline Google (delta + varint base64-like, offset 63).

    Miroir de ``decodePolyline`` côté front (``RoutePreview.tsx``). Retourne une
    liste de ``(lat, lng)``. Une chaîne vide donne une liste vide.
    """
    coords: list[tuple[float, float]] = []
    lat = 0
    lng = 0
    i = 0
    length = len(encoded)
    while i < length:
        result = 0
        shift = 0
        while True:
            b = ord(encoded[i]) - 63
            i += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat

        result = 0
        shift = 0
        while True:
            b = ord(encoded[i]) - 63
            i += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng

        coords.append((lat * 1e-5, lng * 1e-5))
    return coords


def resample_polyline(points: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """Rééchantillonne un tracé en ``n`` points régulièrement espacés (arc-length).

    Bornage du coût de comparaison : deux tracés de tailles différentes sont
    ramenés à ``n`` points pour un Fréchet en ``O(n²)``. Retourne ``points`` tel
    quel si ``n <= 1`` ou si le tracé a moins de 2 points.
    """
    if n <= 1 or len(points) < 2:
        return list(points)
    cumulative = [0.0]
    for i in range(1, len(points)):
        prev_lat, prev_lng = points[i - 1]
        lat, lng = points[i]
        cumulative.append(cumulative[-1] + haversine_m(prev_lat, prev_lng, lat, lng))
    total = cumulative[-1]
    if total <= 0:
        return [points[0]] * n

    out: list[tuple[float, float]] = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j < len(cumulative) - 2 and cumulative[j + 1] < target:
            j += 1
        seg = cumulative[j + 1] - cumulative[j]
        t = 0.0 if seg <= 0 else (target - cumulative[j]) / seg
        lat0, lng0 = points[j]
        lat1, lng1 = points[j + 1]
        out.append((lat0 + (lat1 - lat0) * t, lng0 + (lng1 - lng0) * t))
    return out


def discrete_frechet_m(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float | None:
    """Fréchet discret (en mètres) entre deux tracés de points (lat, lng).

    Mesure la distance minimale de « laisse » qui permet de parcourir les deux
    tracés simultanément — robuste aux tailles différentes et à un sens de
    parcours inverse. Retourne ``None`` si l'un des tracés est vide.
    """
    if not a or not b:
        return None
    n, m = len(a), len(b)
    # DP à deux lignes : ca[j] = coût cumulé jusqu'à (i, j).
    prev = [0.0] * m
    for i in range(n):
        curr = [0.0] * m
        for j in range(m):
            dist = haversine_m(a[i][0], a[i][1], b[j][0], b[j][1])
            if i == 0 and j == 0:
                curr[j] = dist
            elif i == 0:
                curr[j] = max(curr[j - 1], dist)
            elif j == 0:
                curr[j] = max(prev[j], dist)
            else:
                curr[j] = max(min(prev[j], prev[j - 1], curr[j - 1]), dist)
        prev = curr
    return prev[m - 1]


__all__ = [
    "decode_polyline",
    "discrete_frechet_m",
    "haversine_m",
    "resample_polyline",
]
