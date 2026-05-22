import { Link } from "react-router-dom";
import type { DailyBriefResponse } from "../api/types";

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
  if (loading && !data) {
    // Skeleton minimaliste — évite le flash de carte vide.
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

  return (
    <div className="card space-y-3 border-l-4 border-accent">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium uppercase tracking-wider text-muted">
          Briefing du jour
        </h2>
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

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-muted uppercase tracking-wide">TSB</div>
          <div className={`text-base font-semibold ${tsbTone(data.tsb_zone)}`}>
            {tsbLabel}
          </div>
          <div className="text-muted">{data.tsb_zone || "—"}</div>
        </div>
        <div>
          <div className="text-muted uppercase tracking-wide">Aujourd'hui</div>
          {workout.rest_day ? (
            <div className="text-base font-semibold text-muted">Repos</div>
          ) : (
            <>
              <div className="text-base font-semibold text-gray-100 capitalize">
                {workout.kind || "—"}
              </div>
              <div className="text-muted">
                {workout.duration_min != null
                  ? `${workout.duration_min} min`
                  : "—"}
              </div>
            </>
          )}
        </div>
        <div>
          <div className="text-muted uppercase tracking-wide">Alerte</div>
          {data.primary_alert ? (
            <div
              className={`mt-0.5 text-[11px] font-medium ${
                data.primary_alert.severity === "danger"
                  ? "text-red-300"
                  : "text-orange-300"
              }`}
              title={data.primary_alert.message}
            >
              {data.primary_alert.severity === "danger" ? "⚠ Critique" : "⚠ Vigilance"}
            </div>
          ) : (
            <div className="text-base font-semibold text-emerald-300">RAS</div>
          )}
        </div>
      </div>

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
