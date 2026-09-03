import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  RefreshCw,
  Save,
  Sunrise,
  Unlink,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type {
  MorningEntry,
  MorningResponse,
  GoogleHealthStatusResponse,
} from "../api/types";
import MetricCard from "../components/MetricCard";
import { CHART, axisProps, tooltipStyle } from "../chartTheme";
import { useToast } from "../hooks/useToast";

const MANUAL_METRICS: {
  key: keyof MetricForm;
  label: string;
  unit: string;
  isInt?: boolean;
}[] = [
  { key: "hrv_ms", label: "HRV", unit: "ms" },
  { key: "resting_hr", label: "FC repos", unit: "bpm" },
  { key: "sleep_hours", label: "Sommeil", unit: "h" },
  { key: "sleep_score", label: "Score sommeil", unit: "/100", isInt: true },
  { key: "stress_score", label: "Stress", unit: "/100", isInt: true },
];

const ADVANCED_METRICS: {
  key: keyof MorningEntry;
  label: string;
  unit: string;
}[] = [
  { key: "readiness_score", label: "Readiness", unit: "/100" },
  { key: "spo2_avg_pct", label: "SpO2 moyen", unit: "%" },
  { key: "respiratory_rate_avg_bpm", label: "Freq. resp.", unit: "rpm" },
  { key: "skin_temp_delta_c", label: "Δ temp. peau", unit: "°C" },
  { key: "steps", label: "Pas", unit: "" },
  { key: "active_calories", label: "Calories act.", unit: "kcal" },
];

const SLEEP_STAGES: { key: keyof MorningEntry; label: string; color: string }[] = [
  { key: "sleep_deep_min", label: "Deep", color: "#818cf8" },
  { key: "sleep_rem_min", label: "REM", color: "#34d399" },
  { key: "sleep_light_min", label: "Light", color: "#fbbf24" },
  { key: "sleep_awake_min", label: "Awake", color: "#f87171" },
];

interface MetricForm {
  hrv_ms: string;
  resting_hr: string;
  sleep_hours: string;
  sleep_score: string;
  stress_score: string;
}

const EMPTY: MetricForm = {
  hrv_ms: "",
  resting_hr: "",
  sleep_hours: "",
  sleep_score: "",
  stress_score: "",
};

export default function Morning() {
  const [data, setData] = useState<MorningResponse | null>(null);
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [form, setForm] = useState<MetricForm>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [ghStatus, setGhStatus] = useState<GoogleHealthStatusResponse | null>(null);
  const [syncing, setSyncing] = useState(false);
  const { push } = useToast();

  async function refresh() {
    try {
      const [morningData, statusData] = await Promise.all([
        api.morning.get(90),
        api.googleHealth.status(),
      ]);
      setData(morningData);
      setGhStatus(statusData);
      const existing = morningData.history.find((e: MorningEntry) => e.date === date);
      if (existing) {
        setForm({
          hrv_ms: existing.hrv_ms?.toString() ?? "",
          resting_hr: existing.resting_hr?.toString() ?? "",
          sleep_hours: existing.sleep_hours?.toString() ?? "",
          sleep_score: existing.sleep_score?.toString() ?? "",
          stress_score: existing.stress_score?.toString() ?? "",
        });
      } else {
        setForm(EMPTY);
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur : ${msg}`, "error");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  async function submit() {
    setSaving(true);
    try {
      await api.morning.post({
        date,
        hrv_ms: form.hrv_ms ? parseFloat(form.hrv_ms) : null,
        resting_hr: form.resting_hr ? parseFloat(form.resting_hr) : null,
        sleep_hours: form.sleep_hours ? parseFloat(form.sleep_hours) : null,
        sleep_score: form.sleep_score ? parseInt(form.sleep_score, 10) : null,
        stress_score: form.stress_score ? parseInt(form.stress_score, 10) : null,
      });
      push("Entrée enregistrée.", "success");
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  async function syncGoogleHealth() {
    setSyncing(true);
    try {
      const result = await api.googleHealth.sync(7);
      push(result.message, "success");
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Sync échouée : ${msg}`, "error");
    } finally {
      setSyncing(false);
    }
  }

  async function disconnectGoogleHealth() {
    try {
      await api.googleHealth.disconnect();
      push("Google Health déconnecté.", "success");
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Déconnexion échouée : ${msg}`, "error");
    }
  }

  const latestEntry = data?.history[data.history.length - 1] ?? null;

  return (
    <div className="stagger space-y-4">
      <div className="card space-y-3">
        <div className="flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
            <Sunrise className="h-5 w-5 text-accent" strokeWidth={1.75} aria-hidden="true" />
            Google Health
          </h2>
          {ghStatus?.authenticated ? (
            <div className="flex items-center gap-2">
              <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              <span className="text-xs text-muted">Connecté</span>
            </div>
          ) : ghStatus?.configured ? (
            <div className="flex items-center gap-2">
              <span className="inline-flex h-2 w-2 rounded-full bg-amber-500" />
              <span className="text-xs text-muted">Non connecté</span>
            </div>
          ) : null}
        </div>

        {ghStatus?.configured === false && (
          <p className="text-sm text-muted">
            L'intégration Google Health n'est pas configurée côté serveur.
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          {!ghStatus?.authenticated ? (
            <button
              onClick={() => api.googleHealth.auth()}
              className="btn-primary inline-flex items-center gap-2"
            >
              <Activity className="h-4 w-4" strokeWidth={1.75} />
              Connecter Google Health
            </button>
          ) : (
            <>
              <button
                onClick={syncGoogleHealth}
                disabled={syncing}
                className="btn-primary inline-flex items-center gap-2"
              >
                <RefreshCw
                  className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`}
                  strokeWidth={1.75}
                />
                {syncing ? "Sync…" : "Sync maintenant"}
              </button>
              <button
                onClick={disconnectGoogleHealth}
                className="btn-ghost inline-flex items-center gap-2"
              >
                <Unlink className="h-4 w-4" strokeWidth={1.75} />
                Déconnecter
              </button>
            </>
          )}
        </div>

        {ghStatus?.last_sync_at && (
          <p className="text-xs text-muted">
            Dernière sync : {new Date(ghStatus.last_sync_at).toLocaleString("fr-FR")}
          </p>
        )}
      </div>

      {latestEntry && latestEntry.readiness_score != null && (
        <div className="card">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Readiness aujourd'hui</span>
            <ReadinessBadge score={latestEntry.readiness_score} />
          </div>
          <div className="mt-1 font-display text-3xl font-bold">
            {latestEntry.readiness_score}
            <span className="text-base font-normal text-muted">/100</span>
          </div>
        </div>
      )}

      <div className="card space-y-3">
        <h2 className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Sunrise className="h-5 w-5 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Saisie du jour
        </h2>
        <label className="block">
          <span className="text-xs text-muted">Date</span>
          <input
            type="date"
            value={date}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setDate(e.target.value)}
            className="input mt-1"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          {MANUAL_METRICS.map((m) => (
            <label key={m.key} className="block">
              <span className="text-xs text-muted">
                {m.label} <span className="text-muted/70">({m.unit})</span>
              </span>
              <input
                type="number"
                inputMode="decimal"
                step={m.isInt ? 1 : 0.1}
                value={form[m.key]}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, [m.key]: e.target.value }))
                }
                className="input mt-1"
              />
            </label>
          ))}
        </div>
        <button onClick={submit} disabled={saving} className="btn-primary w-full">
          {saving ? (
            "Enregistrement…"
          ) : (
            <span className="inline-flex items-center justify-center gap-2">
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Enregistrer
            </span>
          )}
        </button>
      </div>

      {data && (
        <>
          <h3 className="label-eyebrow">Métriques avancées</h3>
          <div className="grid grid-cols-2 gap-3">
            {ADVANCED_METRICS.map((m) => {
              const value = latestEntry?.[m.key];
              if (value == null) return null;
              return (
                <MetricCard
                  key={m.key}
                  label={m.label}
                  value={`${Number(value).toFixed(m.key === "skin_temp_delta_c" ? 2 : 0)} ${m.unit}`}
                  hint="Google Health"
                />
              );
            })}
          </div>

          {latestEntry && hasSleepStages(latestEntry) && (
            <div className="card space-y-2">
              <h4 className="label-eyebrow">Stades de sommeil (dernière nuit)</h4>
              <div className="grid grid-cols-2 gap-2">
                {SLEEP_STAGES.map((s) => {
                  const min = latestEntry[s.key];
                  if (min == null) return null;
                  return (
                    <div key={s.key} className="flex items-center justify-between rounded-lg bg-white/[0.04] px-3 py-2">
                      <span className="flex items-center gap-2 text-sm">
                        <span
                          className="inline-block h-2 w-2 rounded-full"
                          style={{ backgroundColor: s.color }}
                        />
                        {s.label}
                      </span>
                      <span className="text-sm font-medium">{formatMin(min as number)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <h3 className="label-eyebrow">Tendances 90 j</h3>
          <div className="grid grid-cols-2 gap-3">
            {MANUAL_METRICS.slice(0, 4).map((m) => {
              const b = data.baselines[m.key];
              if (!b || !b.available || b.latest == null) {
                return (
                  <MetricCard
                    key={m.key}
                    label={m.label}
                    value="—"
                    hint={b?.reason || "indisponible"}
                  />
                );
              }
              const delta = b.delta_pct ?? 0;
              const tone =
                m.key === "resting_hr" || m.key === "stress_score"
                  ? delta > 5
                    ? "danger"
                    : "good"
                  : delta < -5
                    ? "danger"
                    : "good";
              return (
                <MetricCard
                  key={m.key}
                  label={m.label}
                  value={`${b.latest.toFixed(1)} ${m.unit}`}
                  hint={`vs base ${b.baseline?.toFixed(1)} ${m.unit}`}
                  badge={{
                    label: `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}%`,
                    tone: tone as "danger" | "good",
                  }}
                />
              );
            })}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {MANUAL_METRICS.slice(0, 4).map((m) => (
              <MorningChart
                key={m.key}
                title={m.label}
                history={data.history as unknown as Record<string, unknown>[]}
                metricKey={m.key}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ReadinessBadge({ score }: { score: number }) {
  let label = "Très faible";
  let tone: "danger" | "warning" | "good" | "accent" = "danger";
  if (score >= 85) {
    label = "Pic";
    tone = "accent";
  } else if (score >= 70) {
    label = "Élevé";
    tone = "good";
  } else if (score >= 50) {
    label = "Équilibré";
    tone = "good";
  } else if (score >= 30) {
    label = "Faible";
    tone = "warning";
  }
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        tone === "danger"
          ? "bg-red-500/10 text-red-500"
          : tone === "warning"
            ? "bg-amber-500/10 text-amber-500"
            : tone === "accent"
              ? "bg-accent/10 text-accent"
              : "bg-emerald-500/10 text-emerald-500"
      }`}
    >
      {label}
    </span>
  );
}

function hasSleepStages(entry: MorningEntry): boolean {
  return (
    entry.sleep_deep_min != null ||
    entry.sleep_rem_min != null ||
    entry.sleep_light_min != null ||
    entry.sleep_awake_min != null
  );
}

function formatMin(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h > 0) return `${h}h${m.toString().padStart(2, "0")}`;
  return `${m} min`;
}

function MorningChart({
  title,
  history,
  metricKey,
}: {
  title: string;
  history: Record<string, unknown>[];
  metricKey: keyof MetricForm;
}) {
  const chartData = history
    .filter((e) => e[metricKey] != null)
    .map((e) => ({ date: e.date as string, value: e[metricKey] as number }));
  if (chartData.length === 0) return null;
  return (
    <div className="card">
      <h4 className="label-eyebrow mb-2">{title}</h4>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => (d as string).slice(5)}
              minTickGap={20}
              {...axisProps}
            />
            <YAxis {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} />
            <Line
              type="monotone"
              dataKey="value"
              stroke={CHART.accent}
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
