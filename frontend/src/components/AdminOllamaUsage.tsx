import { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, RefreshCw } from "lucide-react";
import { api, ApiError } from "../api/client";
import { axisProps, CHART, tooltipItemStyle, tooltipStyle } from "../chartTheme";
import { useToast } from "../hooks/useToast";
import type { AdminOllamaCloud, AdminOllamaUsage as UsageData } from "../api/types";

function errMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(usd: number): string {
  if (usd === 0) return "—";
  return `$${usd.toFixed(usd < 1 ? 4 : 2)}`;
}

function formatMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms)} ms`;
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "imminent";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h} h ${String(m).padStart(2, "0")}`;
  return `${m} min`;
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/[0.06] bg-overlay/[0.02] px-3 py-2">
      <div className="text-lg font-semibold text-fg">{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
      {sub && <div className="text-[11px] text-fg-soft">{sub}</div>}
    </div>
  );
}

const PERIODS = [
  { days: 7, label: "7 j" },
  { days: 30, label: "30 j" },
  { days: 90, label: "90 j" },
];

export default function AdminOllamaUsage() {
  const { push } = useToast();
  const [days, setDays] = useState(30);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [cloud, setCloud] = useState<AdminOllamaCloud | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (d: number) => {
      setLoading(true);
      try {
        const [u, c] = await Promise.all([api.admin.ollamaUsage(d), api.admin.ollamaCloud()]);
        setUsage(u);
        setCloud(c);
      } catch (err) {
        push(`Usage Ollama : ${errMessage(err)}`, "error");
      } finally {
        setLoading(false);
      }
    },
    [push],
  );

  useEffect(() => {
    void load(days);
  }, [days, load]);

  const quotaColor = (pct: number, alertPct: number) =>
    pct >= 100 ? "bg-red-500" : pct >= alertPct ? "bg-amber-500" : "bg-green-500";

  const chartData =
    usage?.by_label.map((b) => ({ name: b.label, appels: b.calls })) ?? [];

  const windows = cloud
    ? [
        { key: "session", title: "Session (5 h)", w: cloud.session },
        { key: "week", title: "Hebdo (7 j)", w: cloud.weekly },
      ]
    : [];

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">Usage Ollama</h3>
        <div className="flex items-center gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              type="button"
              onClick={() => setDays(p.days)}
              className={`rounded-lg border px-2 py-0.5 text-xs ${
                days === p.days
                  ? "border-accent/40 bg-accent/[0.1] text-accent"
                  : "border-border/[0.08] text-muted hover:text-fg-soft"
              }`}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => void load(days)}
            disabled={loading}
            aria-label="Rafraîchir"
            className="btn-ghost ml-1 flex items-center gap-1 px-2 py-1 text-xs disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} strokeWidth={1.75} />
          </button>
        </div>
      </div>

      {usage === null ? (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Chargement…
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Appels" value={usage.totals.calls} />
            <Stat
              label="Tokens"
              value={formatTokens(usage.totals.total_tokens)}
              sub={`${formatTokens(usage.totals.prompt_tokens)} in / ${formatTokens(usage.totals.completion_tokens)} out`}
            />
            <Stat label="Latence moy." value={formatMs(usage.totals.avg_duration_ms)} />
            <Stat
              label="Coût estimé"
              value={formatCost(usage.totals.estimated_cost_usd)}
              sub={usage.totals.errors > 0 ? `${usage.totals.errors} erreur(s)` : undefined}
            />
          </div>

          {chartData.length > 0 && (
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" {...axisProps} allowDecimals={false} />
                  <YAxis dataKey="name" type="category" width={150} {...axisProps} />
                  <Tooltip contentStyle={tooltipStyle} itemStyle={tooltipItemStyle} />
                  <Bar dataKey="appels" fill={CHART.accent} radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="space-y-1">
            <p className="label-eyebrow">Par type d'appel</p>
            <ul className="divide-y divide-border/[0.06] text-xs">
              {usage.by_label.map((b) => (
                <li key={b.key} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="text-fg-soft">{b.label}</span>
                  <span className="text-muted">
                    {b.calls} appels · {formatTokens(b.prompt_tokens + b.completion_tokens)} tok ·{" "}
                    {formatCost(b.estimated_cost_usd)}
                    {b.errors > 0 && <span className="text-red-400"> · {b.errors} err</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {usage.by_actor.length > 0 && (
            <div className="space-y-1">
              <p className="label-eyebrow">Par athlète</p>
              <ul className="divide-y divide-border/[0.06] text-xs">
                {usage.by_actor.map((a) => (
                  <li key={a.public_id ?? "—"} className="flex items-center justify-between gap-3 py-1.5">
                    <span className="text-fg-soft">
                      {a.display_name || a.public_id?.slice(0, 8) || "système"}
                    </span>
                    <span className="text-muted">
                      {a.calls} appels · {formatTokens(a.total_tokens)} tok ·{" "}
                      {formatCost(a.estimated_cost_usd)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {cloud && (
        <div className="space-y-3 border-t border-border/[0.06] pt-3">
          <p className="label-eyebrow">Quota Ollama Cloud (estimation)</p>
          {windows.map(({ key, title, w }) => (
            <div key={key} className="space-y-1">
              <div className="h-3 w-full overflow-hidden rounded-full bg-sunken">
                <div
                  className={`h-full rounded-full ${quotaColor(w.usage_pct, cloud.alert_pct)}`}
                  style={{ width: `${Math.min(100, w.usage_pct)}%` }}
                />
              </div>
              <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
                <span>
                  <span className="text-fg-soft">{title}</span> — {w.usage_pct.toFixed(1)}%
                  {w.quota_units > 0
                    ? ` (${w.units_used}/${w.quota_units} unités)`
                    : " (quota non configuré)"}
                </span>
                <span>
                  {w.requests} req · reset dans {formatCountdown(w.seconds_until_reset)}
                </span>
              </div>
              {w.projected_pct_at_reset != null && (
                <p className="text-[11px] text-muted">
                  Projection à la fin de la fenêtre : {w.projected_pct_at_reset.toFixed(0)}% au rythme
                  actuel.
                </p>
              )}
            </div>
          ))}
          <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
            <span>{cloud.requests} requêtes sur la période</span>
            <span>{cloud.models.length} modèle(s)</span>
          </div>
          <p className="text-xs text-fg-soft">{cloud.recommendation}</p>
          <p className="text-[11px] text-muted">
            Estimation : bornes de fenêtres non documentées par Ollama (ancre communautaire).
          </p>
          {cloud.models.length > 0 && (
            <ul className="divide-y divide-border/[0.06] text-xs">
              {cloud.models.map((m) => (
                <li key={m.model} className="flex items-center justify-between gap-3 py-1">
                  <span className="text-fg-soft">{m.model}</span>
                  <span className="text-muted">
                    {m.requests} req · poids ×{m.weight} · {formatTokens(m.prompt_tokens + m.completion_tokens)} tok
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {usage && usage.recent.length > 0 && (
        <div className="space-y-1 border-t border-border/[0.06] pt-3">
          <p className="label-eyebrow">Derniers appels</p>
          <ul className="divide-y divide-border/[0.06] text-xs">
            {usage.recent.slice(0, 15).map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 py-1">
                <span className="truncate text-fg-soft">
                  {c.label_human} · {c.model ?? "—"}
                </span>
                <span className="shrink-0 text-muted">
                  {c.prompt_tokens ?? 0}+{c.completion_tokens ?? 0} tok · {formatMs(c.total_duration_ms)}
                  {c.status !== "ok" && <span className="text-red-400"> · {c.error_type}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
