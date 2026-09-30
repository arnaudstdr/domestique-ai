"""Tests des utilitaires géométriques du comparateur d'activités."""

from __future__ import annotations

import pytest

from domestique_ai.processing.geo import (
    decode_polyline,
    discrete_frechet_m,
    haversine_m,
    resample_polyline,
)


def test_decode_polyline_matches_reference():
    """Vecteur de référence Google (documentation de l'algorithme)."""
    points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert len(points) == len(expected)
    for got, exp in zip(points, expected, strict=True):
        assert got == pytest.approx(exp, abs=1e-5)


def test_decode_polyline_empty():
    assert decode_polyline("") == []


def test_haversine_zero_for_same_point():
    assert haversine_m(45.0, 6.0, 45.0, 6.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_one_degree_latitude():
    """1° de latitude ≈ 111,2 km."""
    assert haversine_m(45.0, 6.0, 46.0, 6.0) == pytest.approx(111_195, rel=0.01)


def test_haversine_paris_london():
    """Paris → Londres ≈ 343,5 km (ordre de grandeur)."""
    dist = haversine_m(48.8566, 2.3522, 51.5074, -0.1278)
    assert dist == pytest.approx(343_500, rel=0.02)


def test_resample_returns_requested_count_and_endpoints():
    points = [(45.0, 6.0), (45.01, 6.01), (45.02, 6.0), (45.03, 6.02), (45.04, 6.0)]
    resampled = resample_polyline(points, 12)
    assert len(resampled) == 12
    assert resampled[0] == pytest.approx(points[0], abs=1e-9)
    assert resampled[-1] == pytest.approx(points[-1], abs=1e-9)


def test_resample_passthrough_for_short_inputs():
    one = [(45.0, 6.0)]
    assert resample_polyline(one, 10) == one
    two = [(45.0, 6.0), (45.01, 6.01)]
    assert resample_polyline(two, 1) == two


def test_discrete_frechet_identical_is_zero():
    track = [(45.0 + i * 0.001, 6.0 + i * 0.001) for i in range(20)]
    assert discrete_frechet_m(track, track) == pytest.approx(0.0, abs=1e-6)


def test_discrete_frechet_detects_offset():
    a = [(45.0 + i * 0.001, 6.0) for i in range(20)]
    # Décalage de 0,01° en latitude ≈ 1,1 km.
    b = [(45.01 + i * 0.001, 6.0) for i in range(20)]
    assert discrete_frechet_m(a, b) == pytest.approx(1_112, rel=0.05)


def test_discrete_frechet_empty_returns_none():
    assert discrete_frechet_m([], [(45.0, 6.0)]) is None
    assert discrete_frechet_m([(45.0, 6.0)], []) is None
