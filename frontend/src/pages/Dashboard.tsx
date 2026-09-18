import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  RefreshCcw,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type {
  LoadResponse,
  OvertrainingResponse,
  DailyBriefResponse,
  RideVolumeResponse,
  WeeklyVolumeResponse,
} from "../api/types";
import DailyBriefCard from "../components/DailyBriefCard";
import LoadChart from "../components/LoadChart";
import MetricCard from "../components/MetricCard";
import WeeklyVolumeChart from "../components/WeeklyVolumeChart";
import { useToast } from "../hooks/useToast";
import { useViewing } from "../hooks/useViewing";

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
  const [volume, setVolume] = useState<RideVolumeResponse | null>(null);
  const [weekly, setWeekly] = useState<WeeklyVolumeResponse | null>(null);
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const viewing = useViewing();
  const [busy, setBusy] = useState<string | null>(null);
  const { push } = useToast();

  async function refresh() {
    setLoading(true);
    try {
      const [l, o, vol, wk] = await Promise.all([
        api.metrics.load(90),
        api.metrics.overtraining(),
        api.metrics.rideVolume(),
        api.metrics.weeklyVolume(12),
      ]);
      setLoad(l);
      setOt(o);
      setVolume(vol);
      setWeekly(wk);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur de chargement : ${msg}`, "error");
    } finally {
      setLoading(false);
    }
  }

  async function refreshBrief() {
    // Le brief peut prendre quelques secondes (appel LLM). On le charge en
    // parallèle des autres widgets pour ne pas bloquer le rendu, et on
    // affiche un skeleton pendant ce temps.
    setBriefLoading(true);
    try {
      const b = await api.coach.dailyBrief();
      setBrief(b);
    } catch {
      // Silencieux : l'absence de brief ne doit pas perturber le Dashboard.
    } finally {
      setBriefLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    refreshBrief();
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

  return (
    <div className="stagger space-y-4">
      <DailyBriefCard data={brief} loading={briefLoading} />

      {ot && ot.alerts.length > 0 && (
        <div
          className={`card border-l-4 ${
            ot.alerts.some((a) => a.level === "danger")
              ? "border-red-500"
              : "border-orange-500"
          }`}
        >
          <h3 className="flex items-center gap-2 font-medium mb-2 text-sm">
            <TriangleAlert
              className={`h-4 w-4 ${
                ot.alerts.some((a) => a.level === "danger")
                  ? "text-red-400"
                  : "text-orange-400"
              }`}
              strokeWidth={1.75}
              aria-hidden="true"
            />
            Signaux d'alerte
          </h3>
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

      <WeeklyVolumeChart data={weekly?.weeks || []} />

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

      <div className="flex justify-end">
        <Link
          to="/tendances"
          className="text-xs text-accent hover:underline"
        >
          Voir les tendances longues →
        </Link>
      </div>

      {!viewing && (
      <div className="card space-y-3">
        <h3 className="label-eyebrow">Actions</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <button
            className="btn-primary"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Sync Garmin",
                () => api.garmin.sync(),
                () => "Sync Garmin lancée en arrière-plan…",
              )
            }
          >
            {busy === "Sync Garmin" ? (
              "…"
            ) : (
              <span className="inline-flex items-center justify-center gap-2">
                <RefreshCw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                Sync Garmin
              </span>
            )}
          </button>
          <button
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() =>
              triggerAction(
                "Recalculer charge",
                () => api.metrics.recalculate(),
                (r) => {
                  const updated = (r as { updated?: number }).updated ?? 0;
                  return `Recalcul : ${updated} ligne(s) mises à jour`;
                },
              )
            }
          >
            <span className="inline-flex items-center justify-center gap-2">
              <RefreshCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Recalculer
            </span>
          </button>
        </div>
      </div>
      )}

      {loading && <p className="text-center text-sm text-muted">Chargement…</p>}
    </div>
  );
}
