import { useState } from "react";
import { Link } from "react-router-dom";
import { CircleAlert, TriangleAlert } from "lucide-react";
import type { DailyBriefResponse, DailyBriefWorkout } from "../api/types";
import {
  KIND_LABELS,
  KIND_TONES,
  PHASE_LABELS,
  formatDuration,
} from "./workoutKind";

interface Props {
  data: DailyBriefResponse | null;
  loading: boolean;
}

function tsbTone(zone: string | null | undefined): string {
  switch (zone) {
    case "Frais":
      return "text-emerald-300";
    case "Optimal":
      return "text-accent";
    case "Fatigué":
      return "text-orange-300";
    case "Surentraîné":
      return "text-red-300";
    default:
      return "text-muted";
  }
}

function alertTone(severity: "warning" | "danger" | undefined): string {
  if (severity === "danger") return "bg-red-500/10 border-red-500/30 text-red-300";
  if (severity === "warning")
    return "bg-orange-500/10 border-orange-500/30 text-orange-300";
  return "bg-white/5 border-white/10 text-muted";
}

export default function DailyBriefCard({ data, loading }: Props) {
  // L'état d'expansion est local au composant — fermé par défaut, on s'ouvre
  // au clic sur la cellule « Aujourd'hui » quand il y a une séance à détailler.
  const [expanded, setExpanded] = useState(false);

  if (loading && !data) {
    return (
      <div className="card animate-pulse space-y-2">
        <div className="h-3 w-32 rounded bg-white/10" />
        <div className="h-5 w-3/4 rounded bg-white/10" />
        <div className="h-3 w-1/2 rounded bg-white/10" />
      </div>
    );
  }
  if (!data) return null;

  const workout = data.today_workout;
  const tsbLabel =
    data.tsb != null
      ? `${data.tsb >= 0 ? "+" : ""}${data.tsb.toFixed(1)}`
      : "—";
  const hasDetail = !workout.rest_day && workout.structure.length > 0;

  return (
    <div className="card space-y-3 border-l-4 border-accent">
      <div className="flex items-baseline justify-between gap-2">
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

      <p className="text-base text-gray-100 leading-relaxed">{data.summary}</p>

      {data.sleep_hours != null && (
        <div className="rounded-lg border border-white/10 bg-surface/30 px-2.5 py-1.5 text-xs">
          <span className="text-muted uppercase tracking-wide">Sommeil</span>{" "}
          <span className="font-medium text-gray-100">
            {data.sleep_hours.toFixed(1)} h
          </span>
          {data.sleep_baseline != null && data.sleep_delta_pct != null && (
            <span
              className={`ml-1 ${
                data.sleep_delta_pct < -10
                  ? "text-orange-300"
                  : data.sleep_delta_pct < -20
                    ? "text-red-300"
                    : "text-muted"
              }`}
            >
              (moyenne {data.sleep_baseline.toFixed(1)} h ·{" "}
              {data.sleep_delta_pct > 0 ? "+" : ""}
              {data.sleep_delta_pct.toFixed(0)} %)
            </span>
          )}
          {data.sleep_score != null && data.sleep_score < 60 && (
            <span className="ml-1 text-orange-300">
              · qualité basse ({data.sleep_score}/100)
            </span>
          )}
        </div>
      )}

      {data.morning_decision && data.morning_decision !== "go" && (
        <div
          className={`rounded-lg border px-2.5 py-2 text-xs ${
            data.morning_decision === "rest"
              ? "border-yellow-500/25 bg-yellow-500/[0.06] text-yellow-200/90"
              : "border-accent/25 bg-accent/[0.06] text-gray-100"
          }`}
        >
          <span className="font-semibold uppercase tracking-wide">
            Check du matin —{" "}
            {data.morning_decision === "rest" ? "repos complet" : "séance allégée"}
          </span>
          <span className="ml-1">
            {data.morning_reason} L'ajustement est répercuté dans le plan.
          </span>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-muted uppercase tracking-wide">TSB</div>
          <div
            className={`metric-num text-lg font-semibold ${tsbTone(data.tsb_zone)}`}
          >
            {tsbLabel}
          </div>
          <div className="text-muted">{data.tsb_zone || "—"}</div>
        </div>

        <TodayCell
          workout={workout}
          expanded={expanded}
          onToggle={() => hasDetail && setExpanded((v) => !v)}
          hasDetail={hasDetail}
        />

        <div>
          <div className="text-muted uppercase tracking-wide">Alerte</div>
          {data.primary_alert ? (
            <div
              className={`mt-0.5 flex items-center gap-1 text-[11px] font-medium ${
                data.primary_alert.severity === "danger"
                  ? "text-red-300"
                  : "text-orange-300"
              }`}
              title={data.primary_alert.message}
            >
              {data.primary_alert.severity === "danger" ? (
                <>
                  <CircleAlert className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  Critique
                </>
              ) : (
                <>
                  <TriangleAlert className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  Vigilance
                </>
              )}
            </div>
          ) : (
            <div className="text-base font-semibold text-emerald-300">RAS</div>
          )}
        </div>
      </div>

      {expanded && hasDetail && <WorkoutDetail workout={workout} />}

      {data.primary_alert && (
        <div
          className={`rounded-lg border p-2 text-xs ${alertTone(data.primary_alert.severity)}`}
        >
          {data.primary_alert.message}
        </div>
      )}

      <div className="flex justify-end">
        <Link to="/coach" className="text-xs text-accent hover:underline">
          En parler au coach →
        </Link>
      </div>
    </div>
  );
}

interface TodayCellProps {
  workout: DailyBriefWorkout;
  expanded: boolean;
  hasDetail: boolean;
  onToggle: () => void;
}

function TodayCell({ workout, expanded, hasDetail, onToggle }: TodayCellProps) {
  // En repos : pas d'interaction, on affiche juste la mention.
  if (workout.rest_day) {
    return (
      <div>
        <div className="text-muted uppercase tracking-wide">Aujourd'hui</div>
        <div className="text-base font-semibold text-muted">Repos</div>
        {workout.reason && (
          <div className="text-[11px] text-muted leading-tight mt-0.5">
            {workout.reason}
          </div>
        )}
      </div>
    );
  }

  const label =
    (workout.kind && KIND_LABELS[workout.kind]) || workout.kind || "—";
  const tone =
    (workout.kind && KIND_TONES[workout.kind]) || "bg-muted/15 text-muted";

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!hasDetail}
      className="text-left disabled:cursor-default group"
      aria-expanded={expanded}
      title={hasDetail ? "Voir le détail de la séance" : undefined}
    >
      <div className="text-muted uppercase tracking-wide flex items-center gap-1">
        Aujourd'hui
        {hasDetail && (
          <span
            className="text-[10px] text-muted group-hover:text-accent transition-colors"
            aria-hidden
          >
            {expanded ? "▴" : "▾"}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5 mt-0.5">
        <span className={`pill ${tone} text-[10px] py-0.5 px-1.5`}>{label}</span>
      </div>
      <div className="text-muted">
        {workout.duration_min != null ? `${workout.duration_min} min` : "—"}
        {workout.target_zone && (
          <span className="ml-1">· {workout.target_zone.toUpperCase()}</span>
        )}
      </div>
    </button>
  );
}

function WorkoutDetail({ workout }: { workout: DailyBriefWorkout }) {
  return (
    <div className="rounded-lg border border-white/10 bg-surface/40 p-3 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-medium text-gray-100">{workout.name}</div>
        {workout.estimated_tss != null && (
          <span className="text-[11px] text-muted">
            ~{Math.round(workout.estimated_tss)} TSS
          </span>
        )}
      </div>
      {workout.notes && (
        <div className="text-xs italic text-muted">{workout.notes}</div>
      )}
      <ul className="space-y-1 text-xs">
        {workout.structure.slice(0, 5).map((step, idx) => (
          <li
            key={idx}
            className="flex items-center justify-between rounded bg-surface/60 px-2 py-1"
          >
            <span className="text-gray-200">
              <span className="text-muted">
                {PHASE_LABELS[step.phase] || step.phase}
              </span>{" "}
              · {step.zone.toUpperCase()}
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
