import { Link } from "react-router-dom";
import type { ActivitySummary } from "../api/types";
import RoutePreview from "./RoutePreview";

interface Props {
  activity: ActivitySummary;
}

function formatHms(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m.toString().padStart(2, "0")}` : `${m} min`;
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function ActivityCard({ activity }: Props) {
  return (
    <Link
      to={`/activites/${activity.external_id}`}
      className="card flex items-start gap-3 transition-colors hover:bg-cardHover"
    >
      <div className="min-w-0 flex-1">
        <h3 className="truncate font-medium text-gray-100">
          {activity.name || activity.sport_type || "Activité"}
        </h3>
        <p className="mt-0.5 text-xs text-muted">{formatDate(activity.date)}</p>
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <Metric label="Distance" value={`${activity.distance_km.toFixed(1)} km`} />
          <Metric label="Durée" value={formatHms(activity.duration_sec)} />
          <Metric
            label="D+"
            value={
              activity.elevation_m != null
                ? `${Math.round(activity.elevation_m)} m`
                : "—"
            }
          />
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <span className="pill bg-accent/15 text-accent">
          <span className="metric-num">{activity.tss.toFixed(0)}</span> TSS
        </span>
        <RoutePreview polyline={activity.map_polyline} />
      </div>
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className="metric-num text-sm font-medium text-gray-100">{value}</div>
    </div>
  );
}
