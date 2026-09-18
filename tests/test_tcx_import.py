"""Tests du parsing TCX et de l'endpoint d'import ``POST /api/activities/import/tcx``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.db import init_db
from domestique_ai.ingestion.tcx import TcxParseError, parse_tcx

_TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
  <Activities>
    <Activity Sport="Biking">
      <Id>2026-06-01T08:00:00Z</Id>
      <Notes>Sortie test</Notes>
      <Lap StartTime="2026-06-01T08:00:00Z">
        <TotalTimeSeconds>30</TotalTimeSeconds>
        <DistanceMeters>300</DistanceMeters>
        <Calories>20</Calories>
        <Track>
          <Trackpoint>
            <Time>2026-06-01T08:00:00Z</Time>
            <Position><LatitudeDegrees>45.0</LatitudeDegrees><LongitudeDegrees>6.0</LongitudeDegrees></Position>
            <AltitudeMeters>100</AltitudeMeters>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>120</Value></HeartRateBpm>
            <Cadence>80</Cadence>
            <Extensions><ns3:TPX><ns3:Watts>150</ns3:Watts></ns3:TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-06-01T08:00:10Z</Time>
            <Position><LatitudeDegrees>45.001</LatitudeDegrees><LongitudeDegrees>6.001</LongitudeDegrees></Position>
            <AltitudeMeters>110</AltitudeMeters>
            <DistanceMeters>100</DistanceMeters>
            <HeartRateBpm><Value>130</Value></HeartRateBpm>
            <Cadence>85</Cadence>
            <Extensions><ns3:TPX><ns3:Watts>180</ns3:Watts></ns3:TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-06-01T08:00:20Z</Time>
            <AltitudeMeters>105</AltitudeMeters>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
            <Extensions><ns3:TPX><ns3:Watts>210</ns3:Watts></ns3:TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-06-01T08:00:30Z</Time>
            <AltitudeMeters>115</AltitudeMeters>
            <DistanceMeters>300</DistanceMeters>
            <HeartRateBpm><Value>150</Value></HeartRateBpm>
            <Extensions><ns3:TPX><ns3:Watts>240</ns3:Watts></ns3:TPX></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def test_parse_tcx_aggregates() -> None:
    parsed = parse_tcx(_TCX.encode())
    assert len(parsed) == 1
    a = parsed[0]
    assert a.date == "2026-06-01T08:00:00Z"
    assert a.sport_type == "Ride"
    assert a.name == "Sortie test"
    assert a.duration == 30
    assert a.distance == 300
    assert a.elevation_gain == 20  # +10 puis +10
    assert a.elevation_loss == 5  # −5
    assert a.avg_heart_rate == 135
    assert a.max_heart_rate == 150
    assert a.avg_power == 195
    assert a.max_power == 240
    assert a.cadence_avg == 82.5
    assert a.cadence_max == 85
    assert a.calories == 20
    assert a.start_lat == 45.0
    assert a.start_lng == 6.0
    assert a.map_polyline is not None
    assert a.zone_time_stream == [0, 10, 20, 30]
    assert a.zone_hr_stream == [120, 130, 140, 150]


def test_parse_tcx_streams_and_missing_values() -> None:
    a = parse_tcx(_TCX.encode())[0]
    assert a.streams["time"] == [0, 10, 20, 30]
    assert a.streams["heartrate"] == [120, 130, 140, 150]
    assert a.streams["altitude"] == [100, 110, 105, 115]
    assert a.streams["watts"] == [150, 180, 210, 240]
    # Seuls les 2 premiers points ont une position GPS.
    assert a.streams["latlng"] == [[45.0, 6.0], [45.001, 6.001]]


def test_parse_tcx_rejects_invalid_xml() -> None:
    with pytest.raises(TcxParseError):
        parse_tcx(b"<not-xml")


def test_parse_tcx_rejects_empty_document() -> None:
    with pytest.raises(TcxParseError):
        parse_tcx(b"<TrainingCenterDatabase><Activities/></TrainingCenterDatabase>")


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_auth_headers: dict[str, str],
) -> Iterator[TestClient]:
    db = tmp_path / "api_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("STRAVA_HR_REST", "50")
    monkeypatch.setenv("STRAVA_HR_MAX", "190")
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app, headers=api_auth_headers) as c:
        yield c


def test_import_tcx_endpoint_creates_activity(client: TestClient) -> None:
    r = client.post(
        "/api/activities/import/tcx",
        files=[("files", ("ride.tcx", _TCX.encode(), "application/xml"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0
    assert body["errors"] == 0
    result = body["results"][0]
    assert result["status"] == "imported"
    activity = result["activities"][0]
    assert activity["source"] == "tcx"
    assert activity["distance_km"] == 0.3
    assert activity["duration_sec"] == 30
    assert activity["tss"] > 0  # hr-TSS (profil HR configuré)

    external_id = activity["external_id"]

    # Résolution par id local (fallback external_id).
    detail = client.get(f"/api/activities/{external_id}")
    assert detail.status_code == 200
    assert detail.json()["activity"]["source"] == "tcx"

    # Streams persistés servis sur l'endpoint dédié.
    streams = client.get(f"/api/activities/{external_id}/streams")
    assert streams.status_code == 200
    assert streams.json()["heartrate"] == [120, 130, 140, 150]

    # Zones HR calculées depuis les streams + profil HR.
    assert detail.json()["hr_zones"] is not None

    # L'activité apparaît dans la liste.
    listed = client.get("/api/activities?page=1&page_size=20").json()
    assert listed["total"] == 1


def test_import_tcx_endpoint_dedupes_same_file(client: TestClient) -> None:
    files = [("files", ("ride.tcx", _TCX.encode(), "application/xml"))]
    assert client.post("/api/activities/import/tcx", files=files).json()["imported"] == 1
    second = client.post("/api/activities/import/tcx", files=files).json()
    assert second["imported"] == 0
    assert second["skipped"] == 1
    assert second["results"][0]["status"] == "skipped"


def test_import_tcx_endpoint_reports_parse_error(client: TestClient) -> None:
    r = client.post(
        "/api/activities/import/tcx",
        files=[("files", ("broken.tcx", b"<not-xml", "application/xml"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == 1
    assert body["results"][0]["status"] == "error"
