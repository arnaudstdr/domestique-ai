import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { TodayWorkoutResponse } from "../api/types";
import {
  KIND_LABELS,
  KIND_TONES,
  PHASE_LABELS,
  formatDuration,
} from "./workoutKind";

const TSB_ZONE_TONES: Record<string, string> = {
  Frais: "bg-green-500/15 text-green-400",
  Optimal: "bg-accent/15 text-accent",
  Fatigué: "bg-orange-500/15 text-orange-300",
  Surentraîné: "bg-red-500/15 text-red-400",
};

export default function TodayCard() {
  const [data, setData] = useState<TodayWorkoutResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.coach.today();
      setData(r);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">
          🎯 Séance du jour
        </h3>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs text-muted hover:text-accent disabled:opacity-50"
          title="Régénérer la suggestion"
        >
          {loading ? "…" : "🔄"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {!error && loading && !data && (
        <div className="text-sm text-muted">Chargement…</div>
      )}

      {!loading && data?.rest_day && (
        <div className="text-sm text-gray-200">
          <span className="text-2xl">🛌</span>
          <p className="mt-1">{data.reason || "Jour de repos."}</p>
        </div>
      )}

      {!loading && data && !data.rest_day && data.workout && (
        <TodayBody data={data} />
      )}
    </div>
  );
}

function TodayBody({ data }: { data: TodayWorkoutResponse }) {
  const w = data.workout!;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`pill ${KIND_TONES[w.kind] || "bg-muted/15 text-muted"}`}
        >
          {KIND_LABELS[w.kind] || w.kind}
        </span>
        <span className="text-sm text-gray-100">
          {w.duration_min} min · {w.target_zone.toUpperCase()}
        </span>
        <span className="text-xs text-muted">~{Math.round(w.estimated_tss)} TSS</span>
        {data.tsb_zone && (
          <span
            className={`pill ${TSB_ZONE_TONES[data.tsb_zone] || "bg-muted/15 text-muted"}`}
          >
            TSB {data.tsb?.toFixed(1) ?? "—"} · {data.tsb_zone}
          </span>
        )}
      </div>

      <div className="text-sm font-medium text-gray-100">{w.name}</div>
      {w.notes && <div className="text-xs italic text-muted">{w.notes}</div>}

      {w.structure.length > 0 && (
        <ul className="space-y-1 text-xs">
          {w.structure.slice(0, 5).map((s, idx) => (
            <li
              key={idx}
              className="flex items-center justify-between rounded bg-surface/40 px-2 py-1"
            >
              <span className="text-gray-200">
                <span className="text-muted">{PHASE_LABELS[s.phase]}</span> · {s.zone}
              </span>
              <span className="text-muted">{formatDuration(s.duration_sec)}</span>
            </li>
          ))}
          {w.structure.length > 5 && (
            <li className="text-center text-xs text-muted">
              + {w.structure.length - 5} step(s) supplémentaire(s)
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
