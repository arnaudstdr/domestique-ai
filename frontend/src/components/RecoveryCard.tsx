import type { MorningBaseline, MorningResponse } from "../api/types";
import ReadinessBadge from "./ReadinessBadge";
import StatStrip, { type StatItem } from "./StatStrip";

interface Props {
  morning: MorningResponse | null;
  loading: boolean;
}

function deltaBadge(
  baseline: MorningBaseline | undefined,
  higherIsBetter: boolean,
): StatItem["badge"] | undefined {
  if (!baseline || baseline.delta_pct == null) return undefined;
  const delta = baseline.delta_pct;
  const good = higherIsBetter ? delta >= 0 : delta <= 0;
  return {
    label: `${delta >= 0 ? "+" : ""}${delta.toFixed(0)}%`,
    tone: good ? "good" : "warn",
  };
}

function baselineHint(baseline: MorningBaseline | undefined, unit: string): string | undefined {
  if (!baseline || baseline.baseline == null) return undefined;
  return `baseline ${baseline.baseline.toFixed(1)} ${unit}`;
}

export default function RecoveryCard({ morning, loading }: Props) {
  if (loading && !morning) {
    return (
      <div className="card animate-pulse space-y-3">
        <div className="h-3 w-24 rounded bg-overlay/10" />
        <div className="h-8 w-20 rounded bg-overlay/10" />
        <div className="h-3 w-full rounded bg-overlay/10" />
      </div>
    );
  }

  const latest = morning?.history?.[morning.history.length - 1] || null;
  const baselines = morning?.baselines || {};
  const readiness = latest?.readiness_score ?? null;
  const hasData =
    latest != null &&
    (readiness != null ||
      latest.hrv_ms != null ||
      latest.resting_hr != null ||
      latest.sleep_hours != null);

  if (!hasData) {
    return (
      <div className="card min-w-0 space-y-2">
        <h3 className="label-eyebrow">Récupération</h3>
        <p className="text-sm text-muted">Pas encore de données matinales.</p>
      </div>
    );
  }

  const items: StatItem[] = [];
  if (latest?.hrv_ms != null) {
    items.push({
      label: "HRV",
      value: Math.round(latest.hrv_ms).toString(),
      unit: "ms",
      badge: deltaBadge(baselines.hrv_ms, true),
      hint: baselineHint(baselines.hrv_ms, "ms"),
    });
  }
  if (latest?.resting_hr != null) {
    items.push({
      label: "FC repos",
      value: Math.round(latest.resting_hr).toString(),
      unit: "bpm",
      badge: deltaBadge(baselines.resting_hr, false),
      hint: baselineHint(baselines.resting_hr, "bpm"),
    });
  }
  if (latest?.sleep_hours != null) {
    items.push({
      label: "Sommeil",
      value: latest.sleep_hours.toFixed(1),
      unit: "h",
      badge: deltaBadge(baselines.sleep_hours, true),
      hint:
        latest.sleep_score != null
          ? `score ${latest.sleep_score}`
          : baselineHint(baselines.sleep_hours, "h"),
    });
  }

  return (
    <div className="card min-w-0 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="label-eyebrow">Récupération</h3>
        {readiness != null && <ReadinessBadge score={readiness} />}
      </div>

      {readiness != null ? (
        <div className="metric-num text-2xl sm:text-3xl font-bold leading-none text-fg">
          {readiness}
          <span className="text-base font-normal text-muted">/100</span>
        </div>
      ) : (
        <p className="text-sm text-muted">Readiness indisponible</p>
      )}

      {items.length > 0 && (
        <StatStrip columns="3-responsive" items={items} className="pt-1" />
      )}
    </div>
  );
}
