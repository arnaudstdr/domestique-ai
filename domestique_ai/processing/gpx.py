"""
Génération de fichiers GPX 1.1 à partir des streams Strava.

L'API publique Strava ne donne pas accès au .fit original ; on reconstruit
un GPX depuis les streams (lat/lng + altitude + time + heartrate + cadence +
watts). Le fichier produit est importable dans Garmin Connect, Komoot, Zwift,
RideWithGPS, etc.

Le format inclut les extensions Garmin TrackPoint (gpxtpx:hr, gpxtpx:cad)
et la convention <power> pour la puissance.
"""

from __future__ import annotations

import datetime as dt
from xml.etree import ElementTree as ET

GPX_NS = "http://www.topografix.com/GPX/1/1"
GPXTPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = (
    "http://www.topografix.com/GPX/1/1 "
    "http://www.topografix.com/GPX/1/1/gpx.xsd "
    "http://www.garmin.com/xmlschemas/TrackPointExtension/v1 "
    "http://www.garmin.com/xmlschemas/TrackPointExtension/v1/TrackPointExtensionv1.xsd"
)


def build_gpx(
    name: str,
    start_time: dt.datetime,
    streams: dict[str, list],
) -> str:
    """
    Construit un GPX 1.1 à partir des streams Strava.

    Paramètres :
        name : nom de l'activité (utilisé comme métadonnée et nom de trace).
        start_time : datetime de départ (UTC ou aware). Sert de référence
            pour les `time` offsets en secondes.
        streams : dict des streams Strava. Doit contenir au minimum `latlng`
            (liste de [lat, lng]) et `time` (liste de secondes offset).
            Optionnels : `altitude`, `heartrate`, `cadence`, `watts`.

    Retourne le XML sous forme de string (UTF-8, déclaration incluse).

    Lève `ValueError` si `latlng` est absent ou vide.
    """
    latlng = streams.get("latlng")
    if not latlng:
        raise ValueError("Le stream `latlng` est requis pour générer un GPX.")

    times = streams.get("time") or list(range(len(latlng)))
    altitudes = streams.get("altitude") or []
    heartrates = streams.get("heartrate") or []
    cadences = streams.get("cadence") or []
    watts = streams.get("watts") or []

    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=dt.UTC)

    ET.register_namespace("", GPX_NS)
    ET.register_namespace("gpxtpx", GPXTPX_NS)
    ET.register_namespace("xsi", XSI_NS)

    gpx = ET.Element(
        f"{{{GPX_NS}}}gpx",
        attrib={
            "version": "1.1",
            "creator": "DomestiqueAI",
            f"{{{XSI_NS}}}schemaLocation": SCHEMA_LOCATION,
        },
    )

    metadata = ET.SubElement(gpx, f"{{{GPX_NS}}}metadata")
    ET.SubElement(metadata, f"{{{GPX_NS}}}name").text = name
    ET.SubElement(metadata, f"{{{GPX_NS}}}time").text = _iso(start_time)

    trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
    ET.SubElement(trk, f"{{{GPX_NS}}}name").text = name
    trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")

    for i, point in enumerate(latlng):
        if not point or len(point) < 2:
            continue
        lat, lon = point[0], point[1]
        offset = times[i] if i < len(times) else i
        sample_time = start_time + dt.timedelta(seconds=int(offset))

        trkpt = ET.SubElement(
            trkseg,
            f"{{{GPX_NS}}}trkpt",
            attrib={"lat": f"{lat:.7f}", "lon": f"{lon:.7f}"},
        )
        if i < len(altitudes) and altitudes[i] is not None:
            ET.SubElement(trkpt, f"{{{GPX_NS}}}ele").text = f"{altitudes[i]:.2f}"
        ET.SubElement(trkpt, f"{{{GPX_NS}}}time").text = _iso(sample_time)

        hr = heartrates[i] if i < len(heartrates) else None
        cad = cadences[i] if i < len(cadences) else None
        power = watts[i] if i < len(watts) else None

        if hr is not None or cad is not None or power is not None:
            ext = ET.SubElement(trkpt, f"{{{GPX_NS}}}extensions")
            if power is not None:
                ET.SubElement(ext, f"{{{GPX_NS}}}power").text = str(int(power))
            if hr is not None or cad is not None:
                tpx = ET.SubElement(ext, f"{{{GPXTPX_NS}}}TrackPointExtension")
                if hr is not None:
                    ET.SubElement(tpx, f"{{{GPXTPX_NS}}}hr").text = str(int(hr))
                if cad is not None:
                    ET.SubElement(tpx, f"{{{GPXTPX_NS}}}cad").text = str(int(cad))

    ET.indent(gpx, space="  ")
    return ET.tostring(gpx, encoding="unicode", xml_declaration=True)


def _iso(t: dt.datetime) -> str:
    """ISO 8601 UTC avec suffixe Z (format attendu par les imports GPX)."""
    if t.tzinfo is not None:
        t = t.astimezone(dt.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
