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
import { api, ApiError } from "../api/client";
import type { MorningResponse } from "../api/types";
import MetricCard from "../components/MetricCard";
import { useToast } from "../hooks/useToast";

const METRICS: { key: keyof MetricForm; label: string; unit: string; isInt?: boolean }[] = [
  { key: "hrv_ms", label: "HRV", unit: "ms" },
  { key: "resting_hr", label: "FC repos", unit: "bpm" },
  { key: "sleep_hours", label: "Sommeil", unit: "h" },
  { key: "sleep_score", label: "Score sommeil", unit: "/100", isInt: true },
  { key: "stress_score", label: "Stress", unit: "/100", isInt: true },
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
  const { push } = useToast();

  async function refresh() {
    try {
      const r = await api.morning.get(90);
      setData(r);
      const existing = r.history.find((e) => e.date === date);
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

  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <h2 className="text-base font-medium">🌅 Saisie du jour</h2>
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
          {METRICS.map((m) => (
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
        <button
          onClick={submit}
          disabled={saving}
          className="btn-primary w-full"
        >
          {saving ? "Enregistrement…" : "💾 Enregistrer"}
        </button>
      </div>

      {data && (
        <>
          <h3 className="text-sm font-medium text-gray-200">Tendances 90 j</h3>
          <div className="grid grid-cols-2 gap-3">
            {METRICS.slice(0, 4).map((m) => {
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
            {METRICS.slice(0, 4).map((m) => (
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

function MorningChart({
  title,
  history,
  metricKey,
}: {
  title: string;
  history: Record<string, unknown>[];
  metricKey: keyof MetricForm;
}) {
  const data = history
    .filter((e) => e[metricKey] != null)
    .map((e) => ({ date: e.date as string, value: e[metricKey] as number }));
  if (data.length === 0) return null;
  return (
    <div className="card">
      <h4 className="mb-2 text-xs font-medium text-gray-200">{title}</h4>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => (d as string).slice(5)}
              stroke="#9aa3af"
              fontSize={10}
              minTickGap={20}
            />
            <YAxis stroke="#9aa3af" fontSize={10} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#23272f",
                border: "1px solid #2c313a",
                borderRadius: 8,
                color: "#e5e7eb",
                fontSize: 12,
              }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke="#f97316"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
