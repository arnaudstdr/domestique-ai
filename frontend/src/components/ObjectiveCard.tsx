import { Link } from "react-router-dom";
import type { FtpProjectionResponse, Objective } from "../api/types";
import StatStrip, { type StatItem } from "./StatStrip";

interface Props {
  objective: Objective | null;
  projection: FtpProjectionResponse | null;
  loading: boolean;
}

const TYPE_LABELS: Record<Objective["type"], string> = {
  cyclosportive: "Cyclosportive",
  course: "Course",
  cyclo: "Cyclo",
  forme: "Forme",
  maintenance: "Maintenance",
};

const DATE_FR = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

function daysUntil(dateIso: string | null): number | null {
  if (!dateIso) return null;
  const target = new Date(`${dateIso}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

export default function ObjectiveCard({ objective, projection, loading }: Props) {
  if (loading && !objective) {
    return (
      <div className="card animate-pulse space-y-3">
        <div className="h-3 w-20 rounded bg-overlay/10" />
        <div className="h-8 w-16 rounded bg-overlay/10" />
        <div className="h-3 w-full rounded bg-overlay/10" />
      </div>
    );
  }

  if (!objective) {
    return (
      <div className="card min-w-0 space-y-2">
        <h3 className="label-eyebrow">Objectif</h3>
        <p className="text-sm text-muted">Aucun objectif défini.</p>
        <Link to="/plan" className="text-xs text-accent hover:underline">
          Définir un objectif →
        </Link>
      </div>
    );
  }

  const days = daysUntil(objective.date);
  const target = objective.target_ftp;
  const current = projection?.current_ftp ?? null;
  const projected = projection?.projected_ftp ?? null;

  const progressPct =
    target && target > 0 && current != null
      ? Math.min(100, Math.max(0, (current / target) * 100))
      : null;

  const items: StatItem[] = [];
  if (target != null) {
    items.push({ label: "FTP cible", value: Math.round(target).toString(), unit: "W" });
  }
  if (projected != null) {
    items.push({
      label: "FTP projetée",
      value: Math.round(projected).toString(),
      unit: "W",
      hint:
        projection?.projected_wkg != null
          ? `${projection.projected_wkg.toFixed(2)} W/kg`
          : undefined,
    });
  }

  return (
    <div className="card min-w-0 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="label-eyebrow">Objectif</h3>
        <span className="pill shrink-0 bg-accent/10 text-accent">
          {TYPE_LABELS[objective.type]}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        {days != null ? (
          <span className="metric-num text-2xl sm:text-3xl font-bold leading-none text-fg">
            {days >= 0 ? `J-${days}` : "J+"}
          </span>
        ) : (
          <span className="metric-num text-2xl sm:text-3xl font-bold leading-none text-fg">—</span>
        )}
        <span className="text-xs text-muted truncate">
          {objective.date ? DATE_FR.format(new Date(`${objective.date}T00:00:00`)) : "Sans date"}
        </span>
      </div>

      {(objective.distance_km != null || objective.elevation_m != null) && (
        <p className="text-xs text-muted">
          {objective.distance_km != null && `${objective.distance_km} km`}
          {objective.distance_km != null && objective.elevation_m != null && " · "}
          {objective.elevation_m != null && `D+ ${Math.round(objective.elevation_m)} m`}
        </p>
      )}

      {progressPct != null && (
        <div>
          <div className="flex items-baseline justify-between text-[11px] text-muted">
            <span>
              FTP actuelle{" "}
              <span className="metric-num text-fg-soft">{Math.round(current as number)} W</span>
            </span>
            <span>{Math.round(progressPct)}%</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-overlay/10">
            <div className="h-full rounded-full bg-accent" style={{ width: `${progressPct}%` }} />
          </div>
        </div>
      )}

      {items.length > 0 && <StatStrip columns={2} items={items} />}
    </div>
  );
}
