import type { SleepPoint } from "../api/types";

interface Props {
  history: SleepPoint[];
  baseline?: number | null;
}

const DAY_LABELS = ["D", "L", "M", "M", "J", "V", "S"];

function weekdayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? "" : DAY_LABELS[d.getDay()];
}

// Échelle de hauteur : 0 → 10 h. Une nuit « pleine » (≥ 8 h) remplit la barre.
const MAX_HOURS = 10;

/**
 * Mini-barres de sommeil sur 7 jours, avec un repère pointillé matérialisant
 * la baseline 14 j. Les nuits sans donnée restent un slot vide (axe continu).
 */
export default function SleepBars({ history, baseline }: Props) {
  if (!history.length) return null;
  const hasBaseline = baseline != null && baseline > 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <span className="label-eyebrow text-[10px]">Sommeil 7 j</span>
        {hasBaseline && (
          <span className="text-[10px] text-muted">
            ligne = moyenne {baseline!.toFixed(1)} h
          </span>
        )}
      </div>
      <div className="relative flex items-end gap-1 h-12">
        {hasBaseline && (
          <div
            className="pointer-events-none absolute inset-x-0 border-t border-dashed border-border/20"
            style={{ bottom: `${Math.min(100, (baseline! / MAX_HOURS) * 100)}%` }}
          />
        )}
        {history.map((p, i) => {
          const h = p.hours;
          const pct = h == null ? 0 : Math.min(100, (h / MAX_HOURS) * 100);
          const low = h != null && baseline != null && h < baseline - 1;
          return (
            <div key={i} className="flex flex-1 flex-col items-center gap-1">
              <div className="flex h-9 w-full items-end">
                <div
                  className={`w-full rounded-xs ${
                    low ? "bg-orange-400/70" : "bg-accent/70"
                  }`}
                  style={{ height: `${Math.max(pct, h == null ? 0 : 6)}%` }}
                  title={h == null ? "pas de donnée" : `${h.toFixed(1)} h`}
                />
              </div>
              <span className="text-[9px] text-muted">{weekdayLabel(p.date)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
