import { MapContainer, Polyline, TileLayer } from "react-leaflet";
import { LatLngBounds } from "leaflet";

interface Props {
  latlng: [number, number][];
}

export default function ActivityMap({ latlng }: Props) {
  if (!latlng || latlng.length === 0) {
    return (
      <div className="card flex h-56 items-center justify-center text-muted text-sm">
        Pas de trace GPS (indoor / home-trainer).
      </div>
    );
  }
  const bounds = new LatLngBounds(latlng);
  return (
    <div className="card overflow-hidden p-0">
      <MapContainer
        bounds={bounds}
        boundsOptions={{ padding: [20, 20] }}
        style={{ height: 280, width: "100%" }}
        scrollWheelZoom={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Polyline positions={latlng} pathOptions={{ color: "#f97316", weight: 4 }} />
      </MapContainer>
    </div>
  );
}
