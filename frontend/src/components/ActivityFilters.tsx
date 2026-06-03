import { useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import type { ActivityFilters } from "../api/types";

interface Props {
  value: ActivityFilters;
  sportTypes: string[];
  onChange: (next: ActivityFilters) => void;
  onClear: () => void;
}

function activeCount(f: ActivityFilters): number {
  let n = 0;
  if (f.date_from) n++;
  if (f.date_to) n++;
  if (f.sport_types && f.sport_types.length > 0) n++;
  if (f.distance_min_km != null) n++;
  if (f.distance_max_km != null) n++;
  if (f.elevation_min_m != null) n++;
  if (f.elevation_max_m != null) n++;
  if (f.duration_min_sec != null) n++;
  if (f.duration_max_sec != null) n++;
  if (f.tss_min != null) n++;
  if (f.tss_max != null) n++;
  return n;
}

function numOrUndef(s: string): number | undefined {
  if (s === "") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
}

export default function ActivityFilters({
  value,
  sportTypes,
  onChange,
  onClear,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const count = activeCount(value);

  const toggleSport = (sport: string) => {
    const current = value.sport_types ?? [];
    const next = current.includes(sport)
      ? current.filter((s) => s !== sport)
      : [...current, sport];
    onChange({ ...value, sport_types: next.length ? next : undefined });
  };

  const setNumeric = (key: keyof ActivityFilters, raw: string) => {
    const n = numOrUndef(raw);
    onChange({ ...value, [key]: n });
  };

  // Durée stockée en secondes côté API mais affichée en minutes côté UI.
  const durMin = value.duration_min_sec != null
    ? Math.round(value.duration_min_sec / 60)
    : undefined;
  const durMax = value.duration_max_sec != null
    ? Math.round(value.duration_max_sec / 60)
    : undefined;
  const setDuration = (which: "min" | "max", raw: string) => {
    const n = numOrUndef(raw);
    const seconds = n != null ? n * 60 : undefined;
    onChange({
      ...value,
      [which === "min" ? "duration_min_sec" : "duration_max_sec"]: seconds,
    });
  };

  return (
    <div className="card space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-xs text-muted">
          Du
          <input
            type="date"
            className="input"
            value={value.date_from ?? ""}
            onChange={(e) =>
              onChange({ ...value, date_from: e.target.value || undefined })
            }
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Au
          <input
            type="date"
            className="input"
            value={value.date_to ?? ""}
            onChange={(e) =>
              onChange({ ...value, date_to: e.target.value || undefined })
            }
          />
        </label>
      </div>

      {sportTypes.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs uppercase tracking-wider text-muted">
            Sport
          </div>
          <div className="flex flex-wrap gap-1.5">
            {sportTypes.map((sport) => {
              const active = (value.sport_types ?? []).includes(sport);
              return (
                <button
                  key={sport}
                  type="button"
                  onClick={() => toggleSport(sport)}
                  className={
                    "pill text-xs transition-colors " +
                    (active
                      ? "bg-accent text-surface"
                      : "bg-cardHover text-gray-200 hover:bg-cardHover/70")
                  }
                >
                  {sport}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {expanded && (
        <div className="space-y-2 border-t border-cardHover pt-3">
          <RangeRow
            label="Distance (km)"
            minValue={value.distance_min_km}
            maxValue={value.distance_max_km}
            onMin={(v) => setNumeric("distance_min_km", v)}
            onMax={(v) => setNumeric("distance_max_km", v)}
          />
          <RangeRow
            label="D+ (m)"
            minValue={value.elevation_min_m}
            maxValue={value.elevation_max_m}
            onMin={(v) => setNumeric("elevation_min_m", v)}
            onMax={(v) => setNumeric("elevation_max_m", v)}
          />
          <RangeRow
            label="Durée (min)"
            minValue={durMin}
            maxValue={durMax}
            onMin={(v) => setDuration("min", v)}
            onMax={(v) => setDuration("max", v)}
          />
          <RangeRow
            label="TSS"
            minValue={value.tss_min}
            maxValue={value.tss_max}
            onMin={(v) => setNumeric("tss_min", v)}
            onMax={(v) => setNumeric("tss_max", v)}
          />
        </div>
      )}

      <div className="flex items-center justify-between">
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={() => setExpanded((v) => !v)}
        >
          <span className="inline-flex items-center gap-1">
            {expanded ? (
              <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.75} />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
            )}
            Avancé
          </span>
        </button>
        {count > 0 && (
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={onClear}
          >
            <span className="inline-flex items-center gap-1">
              <X className="h-3.5 w-3.5" strokeWidth={1.75} />
              Effacer ({count})
            </span>
          </button>
        )}
      </div>
    </div>
  );
}

interface RangeRowProps {
  label: string;
  minValue: number | undefined;
  maxValue: number | undefined;
  onMin: (raw: string) => void;
  onMax: (raw: string) => void;
}

function RangeRow({ label, minValue, maxValue, onMin, onMax }: RangeRowProps) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-2">
      <span className="text-xs text-muted">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        className="input w-20 text-right text-sm"
        placeholder="min"
        value={minValue ?? ""}
        onChange={(e) => onMin(e.target.value)}
      />
      <input
        type="number"
        inputMode="numeric"
        className="input w-20 text-right text-sm"
        placeholder="max"
        value={maxValue ?? ""}
        onChange={(e) => onMax(e.target.value)}
      />
    </div>
  );
}
