import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  LoadResponse,
  OvertrainingResponse,
  ActivitiesList,
} from "../api/types";
import LoadChart from "../components/LoadChart";
import MetricCard from "../components/MetricCard";
import ZoneBar from "../components/ZoneBar";
import { useToast } from "../hooks/useToast";

function zoneTone(zone: string | undefined) {
  switch (zone) {
    case "freshness":
      return "good" as const;
    case "optimal":
      return "accent" as const;
    case "overreaching":
      return "warn" as const;
    case "overtraining":
      return "danger" as const;
    default:
      return "accent" as const;
  }
}

export default function Dashboard() {
  const [load, setLoad] = useState<LoadResponse | null>(null);
  const [ot, setOt] = useState<OvertrainingResponse | null>(null);
  const [activities, setActivities] = useState<ActivitiesList | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const { push } = useToast();

  async function refresh() {
    setLoading(true);
    try {
      const [l, o, acts] = await Promise.all([
        api.metrics.load(90),
        api.metrics.overtraining(),
        api.activities.list(1, 50, 28),
      ]);
      setLoad(l);
      setOt(o);
      setActivities(acts);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur de chargement : ${msg}`, "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function triggerAction(
    label: string,
    fn: () => Promise<unknown>,
    successHint?: (result: unknown) => string,
  ) {
    setBusy(label);
    try {
      const result = await fn();
      push(successHint ? successHint(result) : `${label} : OK`, "success");
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`${label} : ${msg}`, "error");
    } finally {
      setBusy(null);
    }
  }

  const zones = aggregateZones(activities?.items || []);
  const hasZones = Object.values(zones).some((v) => v > 0);

  return (
    <div className="space-y-4">
      {ot && ot.alerts.length > 0 && (
        <div
          className={`card border-l-4 ${
            ot.alerts.some((a) => a.level === "danger")
              ? "border-red-500"
              : "border-orange-500"
          }`}
        >
          <h3 className="font-medium mb-2 text-sm">🚨 Signaux d'alerte</h3>
          <ul className="space-y-1.5 text-sm">
            {ot.alerts.map((a, i) => (
              <li
                key={i}
                className={
                  a.level === "danger" ? "text-red-300" : "text-orange-300"
                }
              >
                {a.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <MetricCard
          label="CTL"
          value={load?.current ? load.current.ctl.toFixed(1) : "—"}
          hint="Forme (42 j)"
        />
        <MetricCard
          label="ATL"
          value={load?.current ? load.current.atl.toFixed(1) : "—"}
          hint="Fatigue (7 j)"
        />
        <MetricCard
          label="TSB"
          value={load?.current ? load.current.tsb.toFixed(1) : "—"}
          hint="Fraîcheur"
          badge={
            load?.current
              ? { label: load.current.zone_label_fr, tone: zoneTone(load.current.zone) }
              : undefined
          }
        />
      </div>

      <LoadChart data={load?.history || []} />

      {hasZones && <ZoneBar zones={zones} />}

      <div className="card space-y-3">
        <h3 className="text-sm font-medium text-gray-200">Actions</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <button
            className="btn-primary"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Sync Strava",
                () => api.strava.sync(),
                () => "Sync Strava lancée en arrière-plan…",
              )
            }
          >
            {busy === "Sync Strava" ? "…" : "🔄 Sync Strava"}
          </button>
          <button
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Recalculer charge",
                () => api.strava.recalculate(),
                (r) => {
                  const updated = (r as { updated?: number }).updated ?? 0;
                  return `Recalcul : ${updated} ligne(s) mises à jour`;
                },
              )
            }
          >
            🔁 Recalculer
          </button>
          <button
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Backfill zones HR",
                () => api.strava.backfillHrZones(),
                (r) => {
                  const updated = (r as { updated?: number }).updated ?? 0;
                  return `Backfill HR : ${updated} activité(s)`;
                },
              )
            }
          >
            📥 Backfill HR
          </button>
        </div>
      </div>

      {loading && <p className="text-center text-sm text-muted">Chargement…</p>}
    </div>
  );
}

function aggregateZones(items: { hr_zones_sec: Record<string, number | null> | null }[]) {
  const totals: Record<string, number> = { z1: 0, z2: 0, z3: 0, z4: 0, z5: 0 };
  for (const a of items) {
    if (!a.hr_zones_sec) continue;
    for (const key of Object.keys(totals)) {
      totals[key] += Number(a.hr_zones_sec[key] || 0);
    }
  }
  return totals;
}
