"""Tests unitaires pour la génération GPX."""

from __future__ import annotations

import datetime as dt
from xml.etree import ElementTree as ET

import pytest

from domestique_ai.processing.gpx import GPX_NS, GPXTPX_NS, build_gpx


def _ns(tag: str) -> str:
    return f"{{{GPX_NS}}}{tag}"


def _ns_tpx(tag: str) -> str:
    return f"{{{GPXTPX_NS}}}{tag}"


def test_build_gpx_full_streams():
    start = dt.datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    streams = {
        "latlng": [[45.7640, 4.8357], [45.7641, 4.8358], [45.7642, 4.8359]],
        "time": [0, 5, 10],
        "altitude": [200.0, 201.5, 203.0],
        "heartrate": [120, 130, 140],
        "cadence": [80, 85, 88],
        "watts": [150, 200, 250],
    }
    xml = build_gpx("Sortie test", start, streams)

    root = ET.fromstring(xml)
    assert root.tag == _ns("gpx")
    assert root.attrib["creator"] == "DomestiqueAI"

    metadata = root.find(_ns("metadata"))
    assert metadata is not None
    assert metadata.findtext(_ns("name")) == "Sortie test"
    assert metadata.findtext(_ns("time")) == "2026-05-01T08:00:00Z"

    trkpts = root.findall(f"{_ns('trk')}/{_ns('trkseg')}/{_ns('trkpt')}")
    assert len(trkpts) == 3

    first = trkpts[0]
    assert first.attrib["lat"] == "45.7640000"
    assert first.attrib["lon"] == "4.8357000"
    assert first.findtext(_ns("ele")) == "200.00"
    assert first.findtext(_ns("time")) == "2026-05-01T08:00:00Z"

    second = trkpts[1]
    assert second.findtext(_ns("time")) == "2026-05-01T08:00:05Z"
    ext = second.find(_ns("extensions"))
    assert ext is not None
    assert ext.findtext(_ns("power")) == "200"
    tpx = ext.find(_ns_tpx("TrackPointExtension"))
    assert tpx is not None
    assert tpx.findtext(_ns_tpx("hr")) == "130"
    assert tpx.findtext(_ns_tpx("cad")) == "85"


def test_build_gpx_minimal_streams():
    start = dt.datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    streams = {
        "latlng": [[45.0, 4.0], [45.1, 4.1]],
        "time": [0, 60],
    }
    xml = build_gpx("Sortie minimale", start, streams)
    root = ET.fromstring(xml)
    trkpts = root.findall(f"{_ns('trk')}/{_ns('trkseg')}/{_ns('trkpt')}")
    assert len(trkpts) == 2
    assert trkpts[0].find(_ns("ele")) is None
    assert trkpts[0].find(_ns("extensions")) is None


def test_build_gpx_skips_invalid_points():
    start = dt.datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    streams = {
        "latlng": [[45.0, 4.0], [], [45.1, 4.1]],
        "time": [0, 5, 10],
    }
    xml = build_gpx("Sortie", start, streams)
    root = ET.fromstring(xml)
    trkpts = root.findall(f"{_ns('trk')}/{_ns('trkseg')}/{_ns('trkpt')}")
    assert len(trkpts) == 2


def test_build_gpx_naive_datetime_assumed_utc():
    start = dt.datetime(2026, 5, 1, 8, 0, 0)  # naïf
    streams = {"latlng": [[45.0, 4.0]], "time": [0]}
    xml = build_gpx("Sortie", start, streams)
    root = ET.fromstring(xml)
    assert root.find(_ns("metadata")).findtext(_ns("time")) == "2026-05-01T08:00:00Z"


def test_build_gpx_raises_without_latlng():
    start = dt.datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="latlng"):
        build_gpx("X", start, {"time": [0, 1, 2]})

    with pytest.raises(ValueError, match="latlng"):
        build_gpx("X", start, {"latlng": []})
