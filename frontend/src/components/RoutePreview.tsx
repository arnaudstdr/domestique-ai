import { useMemo } from "react";

interface Props {
  polyline: string | null | undefined;
  width?: number;
  height?: number;
  className?: string;
}

/**
 * Décode un polyline Google encodé (format `summary_polyline`) en liste de
 * [lat, lng]. Algorithme classique : delta-encodage + base64-like (offset 63).
 */
function decodePolyline(encoded: string): [number, number][] {
  const coords: [number, number][] = [];
  let lat = 0;
  let lng = 0;
  let i = 0;
  const len = encoded.length;

  while (i < len) {
    let result = 0;
    let shift = 0;
    let b: number;
    do {
      b = encoded.charCodeAt(i++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlat = result & 1 ? ~(result >> 1) : result >> 1;
    lat += dlat;

    result = 0;
    shift = 0;
    do {
      b = encoded.charCodeAt(i++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlng = result & 1 ? ~(result >> 1) : result >> 1;
    lng += dlng;

    coords.push([lat * 1e-5, lng * 1e-5]);
  }
  return coords;
}

export default function RoutePreview({
  polyline,
  width = 88,
  height = 64,
  className = "",
}: Props) {
  const pathD = useMemo(() => {
    if (!polyline) return null;
    let pts: [number, number][];
    try {
      pts = decodePolyline(polyline);
    } catch {
      return null;
    }
    if (pts.length < 2) return null;

    let minLat = Infinity;
    let maxLat = -Infinity;
    let minLng = Infinity;
    let maxLng = -Infinity;
    for (const [la, ln] of pts) {
      if (la < minLat) minLat = la;
      if (la > maxLat) maxLat = la;
      if (ln < minLng) minLng = ln;
      if (ln > maxLng) maxLng = ln;
    }

    // Projection équirectangulaire simple — suffisant pour 80×60 px.
    // On corrige la longitude par cos(latitude moyenne) pour éviter
    // d'aplatir le tracé aux latitudes élevées.
    const midLat = (minLat + maxLat) / 2;
    const cosLat = Math.cos((midLat * Math.PI) / 180);
    const dLat = maxLat - minLat || 1e-9;
    const dLng = (maxLng - minLng) * cosLat || 1e-9;

    // viewBox : on dimensionne en fonction du ratio réel ; SVG fera le fit.
    const PAD = 2;
    const W = width - PAD * 2;
    const H = height - PAD * 2;
    const scale = Math.min(W / dLng, H / dLat);
    const projW = dLng * scale;
    const projH = dLat * scale;
    const offsetX = PAD + (W - projW) / 2;
    const offsetY = PAD + (H - projH) / 2;

    const parts: string[] = [];
    for (let i = 0; i < pts.length; i++) {
      const [la, ln] = pts[i];
      const x = offsetX + (ln - minLng) * cosLat * scale;
      // Inversion Y : latitude croît vers le nord, SVG croît vers le bas.
      const y = offsetY + (maxLat - la) * scale;
      parts.push(`${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return parts.join(" ");
  }, [polyline, width, height]);

  if (!pathD) {
    return (
      <div
        className={`flex items-center justify-center rounded-md bg-cardHover/40 text-[10px] text-muted ${className}`}
        style={{ width, height }}
        aria-hidden
      >
        —
      </div>
    );
  }

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={`rounded-md bg-cardHover/40 text-accent ${className}`}
      aria-hidden
    >
      <path
        d={pathD}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
