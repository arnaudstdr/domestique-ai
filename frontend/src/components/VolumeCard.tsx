import type { RideVolumeResponse, WeeklyVolumeEntry } from "../api/types";
import StatStrip from "./StatStrip";
import WeeklyVolumeChart from "./WeeklyVolumeChart";

interface Props {
  volume: RideVolumeResponse | null;
  weeks: WeeklyVolumeEntry[];
}

function formatKm(km: number, decimals: number): string {
  return km.toLocaleString("fr-FR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function formatElevation(m: number): string {
  return Math.round(m).toLocaleString("fr-FR");
}

function formatHours(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, "0")}`;
}

export default function VolumeCard({ volume, weeks }: Props) {
  const title = `${weeks.length || 12} dernières semaines`;
  return (
    <div className="card space-y-3">
      <h3 className="label-eyebrow">{title}</h3>
      <StatStrip
        items={[
          {
            label: "Année",
            value: volume ? formatKm(volume.year.distance_km, 0) : "—",
            unit: "km",
            hint: volume ? formatHours(volume.year.duration_sec) : undefined,
          },
          {
            label: "Semaine",
            value: volume ? formatKm(volume.week.distance_km, 1) : "—",
            unit: "km",
            hint: volume ? formatHours(volume.week.duration_sec) : undefined,
          },
          {
            label: "D+ semaine",
            value: volume ? formatElevation(volume.week.elevation_m) : "—",
            unit: "m",
          },
        ]}
      />
      <WeeklyVolumeChart embedded data={weeks} />
    </div>
  );
}
