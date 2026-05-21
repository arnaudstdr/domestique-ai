import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  LoadResponse,
  OvertrainingResponse,
  ActivitiesList,
  RideVolumeResponse,
} from "../api/types";
import LoadChart from "../components/LoadChart";
import MetricCard from "../components/MetricCard";
import TodayCard from "../components/TodayCard";
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

function formatKm(km: number): string {
  return `${km.toLocaleString("fr-FR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} km`;
}

function formatHours(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, "0")}`;
}

export default function Dashboard() {
  const [load, setLoad] = useState<LoadResponse | null>(null);
  const [ot, setOt] = useState<OvertrainingResponse | null>(null);
  const [activities, setActivities] = useState<ActivitiesList | null>(null);
  const [volume, setVolume] = useState<RideVolumeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const { push } = useToast();

  async function refresh() {
    setLoading(true);
    try {
      const [l, o, acts, vol] = await Promise.all([
        api.metrics.load(90),
        api.metrics.overtraining(),
        api.activities.list(1, 50, 28),
        api.metrics.rideVolume(),
      ]);
      setLoad(l);
      setOt(o);
      setActivities(acts);
      setVolume(vol);
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

      <TodayCard />

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

      {volume && (
        <div className="grid grid-cols-2 gap-3">
          <MetricCard
            label="Km vélo (année)"
            value={formatKm(volume.year.distance_km)}
            hint={formatHours(volume.year.duration_sec)}
          />
          <MetricCard
            label="Km vélo (semaine)"
            value={formatKm(volume.week.distance_km)}
            hint={formatHours(volume.week.duration_sec)}
          />
        </div>
      )}

      <LoadChart data={load?.history || []} />

      <div className="flex justify-end">
        <Link
          to="/tendances"
          className="text-xs text-accent hover:underline"
        >
          Voir les tendances longues →
        </Link>
      </div>

      {hasZones && <ZoneBar zones={zones} />}

      <div className="card space-y-3">
        <h3 className="text-sm font-medium text-gray-200">Actions</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
          <button
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Backfill température",
                () => api.strava.backfillTemperature(),
                (r) => {
                  const updated = (r as { updated?: number }).updated ?? 0;
                  return `Backfill temp : ${updated} activité(s)`;
                },
              )
            }
          >
            🌡️ Backfill temp.
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
