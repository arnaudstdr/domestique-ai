"""Tests du parser Garmin index-aligned + compaction (base des montées)."""

from __future__ import annotations

from domestique_ai.ingestion.garmin import (
    compact_aligned_series,
    parse_details_aligned,
)


def _modern_details(rows: list[dict[str, float | None]]) -> dict:
    keys = sorted({key for row in rows for key in row})
    descriptors = [{"key": key, "metricsIndex": idx} for idx, key in enumerate(keys)]
    samples = [{"metrics": [row.get(key) for key in keys]} for row in rows]
    return {"metricDescriptors": descriptors, "activityDetailMetrics": samples}


def test_parse_details_aligned_keeps_holes_aligned():
    details = _modern_details(
        [
            {
                "sumDistance": 0.0,
                "directElevation": 100.0,
                "sumDuration": 0.0,
                "directHeartRate": 120,
            },
            {
                "sumDistance": 10.0,
                "directElevation": None,
                "sumDuration": 5.0,
                "directHeartRate": None,
            },
            {
                "sumDistance": 20.0,
                "directElevation": 101.0,
                "sumDuration": 10.0,
                "directHeartRate": 121,
            },
        ]
    )
    series = parse_details_aligned(details)
    assert len(series["time"]) == len(series["altitude"]) == len(series["distance"]) == 3
    assert series["altitude"][1] is None
    assert series["heartrate"][1] is None
    assert series["altitude"][2] == 101.0
    assert series["time"] == [0.0, 5.0, 10.0]


def test_parse_details_aligned_time_falls_back_to_timestamp():
    details = _modern_details(
        [
            {"directTimestamp": 1_700_000_000_000, "directElevation": 100.0, "sumDistance": 0.0},
            {"directTimestamp": 1_700_000_005_000, "directElevation": 101.0, "sumDistance": 10.0},
        ]
    )
    series = parse_details_aligned(details)
    assert series["time"] == [0.0, 5.0]


def test_parse_details_aligned_legacy_orientation_a():
    details = {
        "metricsEntries": [
            {"metricDescriptorDTOs": [{"key": "directElevation"}], "metrics": [100.0, None, 102.0]},
            {"metricDescriptorDTOs": [{"key": "sumDistance"}], "metrics": [0.0, 10.0, 20.0]},
            {"metricDescriptorDTOs": [{"key": "sumDuration"}], "metrics": [0.0, 5.0, 10.0]},
        ]
    }
    series = parse_details_aligned(details)
    assert series["altitude"] == [100.0, None, 102.0]
    assert series["distance"] == [0.0, 10.0, 20.0]


def test_compact_aligned_series_samples_every_step():
    time = [i * 5.0 for i in range(20)]
    payload = {"time": time, "altitude": [300.0 + i for i in range(20)]}
    compact = compact_aligned_series(payload, step_sec=5.0)
    assert compact["time"] == time
    assert compact["altitude"] == payload["altitude"]


def test_compact_aligned_series_keeps_first_and_last():
    payload = {"time": [float(i) for i in range(100)], "altitude": [float(i) for i in range(100)]}
    compact = compact_aligned_series(payload, step_sec=5.0)
    assert compact["time"][0] == 0.0
    assert compact["time"][-1] == 99.0
    assert compact["time"][1] == 5.0
    assert compact["altitude"][-1] == 99.0


def test_compact_aligned_series_respects_max_points():
    payload = {"time": [float(i) for i in range(10_000)]}
    compact = compact_aligned_series(payload, step_sec=1.0, max_points=100)
    assert len(compact["time"]) <= 100
    assert compact["time"][0] == 0.0
    assert compact["time"][-1] == 9999.0
