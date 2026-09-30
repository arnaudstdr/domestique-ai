import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, CircleAlert, Lightbulb, TriangleAlert } from "lucide-react";
import type {
  DailyBriefResponse,
  DailyBriefWorkout,
  OvertrainingIndicators,
} from "../api/types";
import CoachAvatar from "./CoachAvatar";
import SleepBars from "./SleepBars";
import TsbGauge, { zoneColor } from "./TsbGauge";
import {
  KIND_LABELS,
  KIND_TONES,
  PHASE_LABELS,
  formatDuration,
} from "./workoutKind";

interface Props {
  data: DailyBriefResponse | null;
  loading: boolean;
  /** Alertes overtraining brutes (message + niveau) fusionnées dans la surface unique. */
  secondaryAlerts?: { message: string; level: "warning" | "danger" }[];
  /** Indicateurs overtraining chiffrés — affichés uniquement en présence d'alerte. */
  indicators?: OvertrainingIndicators | null;
}

// Teinte d'ambiance par zone TSB : un halo diffus en fond de hero qui « colore »
// l'ouverture de l'app selon l'état du jour.
const ZONE_GLOW: Record<string, string> = {
  Frais: "52 211 153",
  Optimal: "199 242 74",
  Fatigué: "245 165 36",
  Surentraîné: "255 93 93",
};

function alertTone(severity: "warning" | "danger" | undefined): string {
  if (severity === "danger") return "bg-red-500/10 border-red-500/30 text-red-300";
  if (severity === "warning")
    return "bg-orange-500/10 border-orange-500/30 text-orange-300";
  return "bg-overlay/5 border-border/10 text-muted";
}

export default function DailyBriefCard({
  data,
  loading,
  secondaryAlerts = [],
  indicators = null,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);

  if (loading && !data) {
    return (
      <div className="card animate-pulse space-y-3">
        <div className="h-3 w-32 rounded bg-overlay/10" />
        <div className="h-5 w-3/4 rounded bg-overlay/10" />
        <div className="h-3 w-1/2 rounded bg-overlay/10" />
      </div>
    );
  }
  if (!data) return null;

  const workout = data.today_workout;
  const hasDetail = !workout.rest_day && workout.structure.length > 0;
  const glow = ZONE_GLOW[data.tsb_zone || ""] || null;
  const zoneHex = zoneColor(data.tsb_zone);

  return (
    <div className="card relative overflow-hidden space-y-4 border-l-4 border-accent">
      {/* Halo d'ambiance teinté par l'état du jour. */}
      {glow && (
        <div
          className="pointer-events-none absolute inset-0 -z-10"
          aria-hidden="true"
          style={{
            background: `radial-gradient(120% 90% at 100% 0%, rgb(${glow} / 0.16), transparent 60%)`,
          }}
        />
      )}

      <div className="flex items-center justify-between gap-2">
        <h2 className="label-eyebrow">Briefing du jour</h2>
        <span
          className="text-[10px] text-muted"
          title={
            data.source === "llm"
              ? "Phrase générée par le coach"
              : data.source === "cache"
                ? "Réponse mise en cache pour la journée"
                : "Fallback déterministe (Ollama indisponible)"
          }
        >
          {data.source === "llm" ? "coach" : data.source}
        </span>
      </div>

      {/* Rangée principale : jauge TSB + verdict du coach. */}
      <div className="flex items-center gap-4">
        <TsbGauge value={data.tsb} zone={data.tsb_zone} />
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="flex items-start gap-2.5">
            <CoachAvatar size={30} />
            <p
              className={`flex-1 text-sm text-fg leading-relaxed ${
                summaryOpen ? "" : "line-clamp-2"
              }`}
            >
              {data.summary}
            </p>
          </div>
          {data.summary.length > 90 && (
            <button
              type="button"
              onClick={() => setSummaryOpen((v) => !v)}
              className="text-[11px] text-accent hover:underline"
            >
              {summaryOpen ? "Réduire" : "Lire la suite"}
            </button>
          )}

          {/* Séance du jour en barre : durée + TSS estimé, colorée par zone cible. */}
          <TodayBar
            workout={workout}
            expanded={expanded}
            hasDetail={hasDetail}
            onToggle={() => hasDetail && setExpanded((v) => !v)}
          />
        </div>
      </div>

      {data.coach_tip && (
        <div className="flex items-start gap-2 rounded-lg border border-accent/20 bg-accent/[0.05] px-3 py-2 text-xs text-fg">
          <Lightbulb
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          <span>{data.coach_tip}</span>
        </div>
      )}

      {expanded && hasDetail && <WorkoutDetail workout={workout} />}

      <div className="grid grid-cols-2 gap-4">
        {/* Charge hebdo : TSS réalisé / planifié + adhérence du plan. */}
        <div>
          <div className="label-eyebrow text-[10px]">Semaine</div>
          {data.week_tss_planned != null ? (
            <>
              <div className="metric-num text-lg font-semibold text-fg">
                {Math.round(data.week_tss_done ?? 0)}
                <span className="text-muted text-sm">
                  {" "}
                  / {Math.round(data.week_tss_planned)} TSS
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-overlay/10">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{
                    width: `${Math.min(
                      100,
                      ((data.week_tss_done ?? 0) / (data.week_tss_planned || 1)) * 100,
                    )}%`,
                  }}
                />
              </div>
              {data.week_adherence_pct != null && (
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted">
                  <span className="metric-num text-fg-soft">
                    {Math.round(data.week_adherence_pct)}%
                  </span>
                  <span>adhérence</span>
                  <WeekStatuses
                    done={data.week_done}
                    partial={data.week_partial}
                    missed={data.week_missed}
                    skipped={data.week_skipped}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="text-sm text-muted">Aucun plan actif</div>
          )}
        </div>

        {/* Mini-barres sommeil 7 j + baseline. */}
        <SleepBars history={data.sleep_history} baseline={data.sleep_baseline} />
      </div>

      {/* Surface unique d'alertes : primaire toujours visible, secondaires dépliables. */}
      <AlertSurface
        primary={data.primary_alert}
        secondary={secondaryAlerts}
        indicators={indicators}
        open={alertsOpen}
        onToggle={() => setAlertsOpen((v) => !v)}
        accentColor={zoneHex}
      />

      <div className="flex items-center justify-between">
        {(data.ctl != null || data.atl != null) && (
          <span className="metric-num text-[11px] text-muted">
            CTL {data.ctl?.toFixed(1) ?? "—"} · ATL {data.atl?.toFixed(1) ?? "—"}
          </span>
        )}
        <Link to="/coach" className="ml-auto text-xs text-accent hover:underline">
          En parler au coach →
        </Link>
      </div>
    </div>
  );
}

interface TodayBarProps {
  workout: DailyBriefWorkout;
  expanded: boolean;
  hasDetail: boolean;
  onToggle: () => void;
}

function TodayBar({ workout, expanded, hasDetail, onToggle }: TodayBarProps) {
  if (workout.rest_day) {
    return (
      <div className="rounded-lg border border-border/10 bg-surface/40 px-3 py-2 text-xs">
        <span className="text-muted uppercase tracking-wide">Aujourd'hui</span>{" "}
        <span className="font-semibold text-muted">Repos</span>
        {workout.reason && <div className="mt-0.5 text-[11px] text-muted">{workout.reason}</div>}
      </div>
    );
  }

  const label = (workout.kind && KIND_LABELS[workout.kind]) || workout.kind || "—";
  const tone = (workout.kind && KIND_TONES[workout.kind]) || "bg-muted/15 text-muted";

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!hasDetail}
      aria-expanded={expanded}
      className="group w-full rounded-lg border border-border/10 bg-surface/40 px-3 py-2 text-left disabled:cursor-default enabled:hover:border-accent/40 transition-colors"
      title={hasDetail ? "Voir le détail de la séance" : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted uppercase tracking-wide text-[10px]">Aujourd'hui</span>
        {hasDetail && (
          <ChevronDown
            className={`h-3.5 w-3.5 text-muted transition-transform group-hover:text-accent ${
              expanded ? "rotate-180" : ""
            }`}
            strokeWidth={1.75}
            aria-hidden="true"
          />
        )}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className={`pill ${tone} text-[10px] py-0.5 px-1.5`}>{label}</span>
        <span className="metric-num text-xs text-fg-soft">
          {workout.duration_min != null ? `${workout.duration_min} min` : "—"}
          {workout.target_zone && (
            <span className="ml-1 text-muted">· {workout.target_zone.toUpperCase()}</span>
          )}
        </span>
        {workout.estimated_tss != null && (
          <span className="ml-auto metric-num text-[11px] text-muted">
            ~{Math.round(workout.estimated_tss)} TSS
          </span>
        )}
      </div>
    </button>
  );
}

interface AlertSurfaceProps {
  primary: DailyBriefResponse["primary_alert"];
  secondary: { message: string; level: "warning" | "danger" }[];
  indicators?: OvertrainingIndicators | null;
  open: boolean;
  onToggle: () => void;
  accentColor: string;
}

function AlertSurface({
  primary,
  secondary,
  indicators = null,
  open,
  onToggle,
  accentColor,
}: AlertSurfaceProps) {
  const total = (primary ? 1 : 0) + secondary.length;
  if (total === 0) {
    return (
      <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2 text-xs text-emerald-300">
        Aucun signal d'alerte aujourd'hui.
      </div>
    );
  }

  const danger = primary?.severity === "danger" || secondary.some((a) => a.level === "danger");
  const primaryTone = alertTone(danger ? "danger" : "warning");

  const chips: { label: string; value: string }[] = [];
  if (indicators) {
    if (indicators.chronic_tsb != null) {
      const v = indicators.chronic_tsb;
      chips.push({ label: "TSB chron.", value: `${v >= 0 ? "+" : ""}${v.toFixed(1)}` });
    }
    if (indicators.monotony != null) {
      chips.push({ label: "Monotonie", value: indicators.monotony.toFixed(1) });
    }
    if (indicators.strain != null) {
      chips.push({ label: "Strain", value: Math.round(indicators.strain).toString() });
    }
    if (indicators.weekly_jump_pct != null) {
      const v = indicators.weekly_jump_pct;
      chips.push({ label: "Volume", value: `${v >= 0 ? "+" : ""}${v.toFixed(0)}%` });
    }
  }

  return (
    <div className={`rounded-lg border p-2 text-xs ${primaryTone}`}>
      <div className="flex items-center gap-1.5 font-medium">
        {danger ? (
          <>
            <CircleAlert
              className="h-3.5 w-3.5 animate-alert-pulse"
              strokeWidth={1.75}
              aria-hidden="true"
              style={{ color: accentColor }}
            />
            Alerte critique
          </>
        ) : (
          <>
            <TriangleAlert className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            Vigilance
          </>
        )}
        <button
          type="button"
          onClick={onToggle}
          className="ml-auto flex items-center gap-1 text-[10px] opacity-80 hover:opacity-100"
        >
          {total} signal{total > 1 ? "x" : ""}
          <ChevronDown
            className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </button>
      </div>
      {primary && <div className="mt-1">{primary.message}</div>}
      {chips.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span key={c.label} className="rounded bg-overlay/10 px-1.5 py-0.5 text-[10px]">
              <span className="opacity-70">{c.label}</span>{" "}
              <span className="metric-num font-medium">{c.value}</span>
            </span>
          ))}
        </div>
      )}
      {open && secondary.length > 0 && (
        <ul className="mt-1.5 space-y-1 border-t border-border/10 pt-1.5">
          {secondary.map((a, i) => (
            <li key={i} className={a.level === "danger" ? "text-red-300" : "text-orange-300"}>
              {a.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const WEEK_STATUS_DOTS = [
  { key: "done", color: "bg-emerald-500", label: "faites" },
  { key: "partial", color: "bg-amber-500", label: "partielles" },
  { key: "missed", color: "bg-red-500", label: "manquées" },
  { key: "skipped", color: "bg-sky-400", label: "repos coach" },
] as const;

interface WeekStatusesProps {
  done: number | null;
  partial: number | null;
  missed: number | null;
  skipped: number | null;
}

function WeekStatuses({ done, partial, missed, skipped }: WeekStatusesProps) {
  const values: Record<(typeof WEEK_STATUS_DOTS)[number]["key"], number | null> = {
    done,
    partial,
    missed,
    skipped,
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {WEEK_STATUS_DOTS.map(({ key, color, label }) => {
        const value = values[key];
        if (!value) return null;
        return (
          <span key={key} className="inline-flex items-center gap-0.5" title={label}>
            <span className={`h-1.5 w-1.5 rounded-full ${color}`} aria-hidden="true" />
            <span className="metric-num">{value}</span>
          </span>
        );
      })}
    </span>
  );
}

function WorkoutDetail({ workout }: { workout: DailyBriefWorkout }) {
  return (
    <div className="rounded-lg border border-border/10 bg-surface/40 p-3 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-medium text-fg">{workout.name}</div>
        {workout.estimated_tss != null && (
          <span className="text-[11px] text-muted">~{Math.round(workout.estimated_tss)} TSS</span>
        )}
      </div>
      {workout.notes && <div className="text-xs italic text-muted">{workout.notes}</div>}
      <ul className="space-y-1 text-xs">
        {workout.structure.slice(0, 5).map((step, idx) => (
          <li
            key={idx}
            className="flex items-center justify-between rounded bg-surface/60 px-2 py-1"
          >
            <span className="text-fg-soft">
              <span className="text-muted">{PHASE_LABELS[step.phase] || step.phase}</span> ·{" "}
              {step.zone.toUpperCase()}
            </span>
            <span className="text-muted">{formatDuration(step.duration_sec)}</span>
          </li>
        ))}
        {workout.structure.length > 5 && (
          <li className="text-center text-[11px] text-muted">
            + {workout.structure.length - 5} step(s) supplémentaire(s)
          </li>
        )}
      </ul>
    </div>
  );
}
