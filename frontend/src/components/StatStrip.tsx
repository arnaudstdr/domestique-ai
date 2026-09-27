import { BADGE_TONES } from "./MetricCard";

export interface StatItem {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  badge?: { label: string; tone?: "accent" | "good" | "warn" | "danger" };
}

interface Props {
  items: StatItem[];
  columns?: 2 | 3 | 4;
  className?: string;
}

const GRID_COLS: Record<number, string> = {
  2: "grid-cols-2",
  3: "grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
};

export default function StatStrip({ items, columns = 3, className = "" }: Props) {
  return (
    <div className={`grid ${GRID_COLS[columns]} gap-x-3 gap-y-3 ${className}`}>
      {items.map((item, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-0.5">
          <div className="flex items-center justify-between gap-2">
            <span className="label-eyebrow truncate">{item.label}</span>
            {item.badge && (
              <span
                className={`pill shrink-0 ${
                  BADGE_TONES[item.badge.tone || "accent"] || BADGE_TONES.accent
                }`}
              >
                {item.badge.label}
              </span>
            )}
          </div>
          <span className="flex items-baseline gap-1">
            <span className="metric-num text-xl sm:text-2xl font-semibold leading-none text-gray-50 whitespace-nowrap">
              {item.value}
            </span>
            {item.unit && <span className="text-xs text-muted">{item.unit}</span>}
          </span>
          {item.hint && <span className="text-[11px] text-muted truncate">{item.hint}</span>}
        </div>
      ))}
    </div>
  );
}
