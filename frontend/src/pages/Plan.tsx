import { useEffect, useState } from "react";
import {
  Bot,
  Calendar,
  ClipboardList,
  Download,
  Library,
  Sparkles,
  Target,
  Trash2,
} from "lucide-react";
import {
  api,
  ApiError,
  streamLlmPlan,
  type LlmPlanEvent,
} from "../api/client";
import type { Objective, PlanDetail, PlanSummary, Workout } from "../api/types";
import PlanCalendar from "../components/PlanCalendar";
import { useToast } from "../hooks/useToast";
import { useViewing } from "../hooks/useViewing";

type GenerationMode = "classic" | "llm";

interface LlmWeekProgress {
  index: number;
  source: "llm" | "fallback";
  adjustments: string[];
  workouts: Workout[];
}

interface LlmStreamState {
  ctlCurrent: number | null;
  targetDate: string | null;
  weeks: LlmWeekProgress[];
  summary: {
    planId: number | null;
    totalWorkouts: number;
    llmWeeks: number;
    fallbackWeeks: number;
  } | null;
  error: string | null;
}

const EMPTY_LLM_STREAM: LlmStreamState = {
  ctlCurrent: null,
  targetDate: null,
  weeks: [],
  summary: null,
  error: null,
};

const OBJECTIVE_LABELS: Record<Objective["type"], string> = {
  cyclosportive: "Cyclosportive",
  course: "Course",
  cyclo: "Cyclo",
  maintenance: "Maintenance",
};

function daysUntil(dateStr: string): number | null {
  const target = new Date(dateStr + "T00:00:00");
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function formatCreatedAt(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function describePlan(plan: PlanSummary): string {
  const date = formatCreatedAt(plan.created_at);
  const weeks = plan.weeks ? `${plan.weeks} sem` : "—";
  const target = plan.target_date
    ? ` → ${plan.target_date}`
    : plan.target_event_type
      ? ` (${plan.target_event_type})`
      : "";
  return `${date} · ${weeks}${target}`;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function Plan() {
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [objective, setObjective] = useState<Objective | null>(null);
  const [sessionsPerWeek, setSessionsPerWeek] = useState(4);
  const [focus, setFocus] = useState("");
  const [generating, setGenerating] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadingIcs, setDownloadingIcs] = useState(false);
  const [mode, setMode] = useState<GenerationMode>("classic");
  const [llmStream, setLlmStream] = useState<LlmStreamState>(EMPTY_LLM_STREAM);
  const { push } = useToast();
  const viewing = useViewing();

  async function refreshList(autoSelect = true): Promise<void> {
    try {
      const list = await api.plan.list(50);
      setPlans(list);
      if (autoSelect && list.length > 0 && selectedId == null) {
        setSelectedId(list[0].id);
      }
      if (list.length === 0) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur chargement plans : ${msg}`, "error");
    }
  }

  useEffect(() => {
    refreshList(true);
    api.objective
      .get()
      .then((obj) => setObjective(obj))
      .catch((err) => {
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Erreur chargement objectif : ${msg}`, "error");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    api.plan
      .detail(selectedId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Erreur chargement plan : ${msg}`, "error");
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, push]);

  async function generate(): Promise<void> {
    if (mode === "llm") {
      await generateLlm();
      return;
    }
    setGenerating(true);
    try {
      const created = await api.plan.create({
        sessions_per_week: sessionsPerWeek,
        focus: focus.trim() || null,
      });
      push(`Plan généré : ${created.workouts.length} séances.`, "success");
      setFocus("");
      setSelectedId(created.id);
      setDetail(created);
      await refreshList(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur génération : ${msg}`, "error");
    } finally {
      setGenerating(false);
    }
  }

  async function generateLlm(): Promise<void> {
    setGenerating(true);
    setLlmStream({ ...EMPTY_LLM_STREAM });
    try {
      await streamLlmPlan(
        { sessions_per_week: sessionsPerWeek, focus: focus.trim() || null },
        (ev: LlmPlanEvent) => {
          setLlmStream((prev) => {
            if (ev.type === "start") {
              return {
                ...EMPTY_LLM_STREAM,
                ctlCurrent: ev.ctl_current,
                targetDate: ev.target_date,
              };
            }
            if (ev.type === "week_completed") {
              return {
                ...prev,
                weeks: [
                  ...prev.weeks,
                  {
                    index: ev.index,
                    source: ev.source,
                    adjustments: ev.adjustments,
                    workouts: ev.workouts,
                  },
                ],
              };
            }
            if (ev.type === "error") {
              return { ...prev, error: ev.value };
            }
            if (ev.type === "done") {
              return {
                ...prev,
                summary: {
                  planId: ev.plan_id,
                  totalWorkouts: ev.total_workouts ?? 0,
                  llmWeeks: ev.llm_weeks ?? 0,
                  fallbackWeeks: ev.fallback_weeks ?? 0,
                },
              };
            }
            return prev;
          });
        },
      );
      // Une fois le streaming terminé, on rafraîchit la liste et sélectionne le plan.
      const list = await api.plan.list(50);
      setPlans(list);
      const newest = list[0];
      if (newest) {
        setSelectedId(newest.id);
        const fresh = await api.plan.detail(newest.id);
        setDetail(fresh);
      }
      push("Plan généré par le coach IA.", "success");
      setFocus("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur génération IA : ${msg}`, "error");
      setLlmStream((prev) => ({ ...prev, error: msg }));
    } finally {
      setGenerating(false);
    }
  }

  async function remove(): Promise<void> {
    if (selectedId == null) return;
    if (!window.confirm("Supprimer ce plan ?")) return;
    setDeleting(true);
    try {
      await api.plan.remove(selectedId);
      push("Plan supprimé.", "success");
      setSelectedId(null);
      setDetail(null);
      await refreshList(true);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur suppression : ${msg}`, "error");
    } finally {
      setDeleting(false);
    }
  }

  async function downloadZip(): Promise<void> {
    if (selectedId == null) return;
    setDownloading(true);
    try {
      const { blob, filename } = await api.plan.exportZip(selectedId);
      triggerDownload(blob, filename);
      push(`Téléchargement : ${filename}`, "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur téléchargement : ${msg}`, "error");
    } finally {
      setDownloading(false);
    }
  }

  async function downloadIcs(): Promise<void> {
    if (selectedId == null) return;
    setDownloadingIcs(true);
    try {
      const { blob, filename } = await api.plan.exportIcs(selectedId);
      triggerDownload(blob, filename);
      push(`Téléchargement : ${filename}`, "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur téléchargement : ${msg}`, "error");
    } finally {
      setDownloadingIcs(false);
    }
  }

  const remainingDays = objective?.date ? daysUntil(objective.date) : null;

  return (
    <div className="stagger space-y-4">
      <div className="card space-y-2">
        <h2 className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Target className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Objectif courant
        </h2>
        {!objective ? (
          <div className="text-sm text-muted">
            Aucun objectif défini. Le plan sera généré en mode « maintenance ».
            Renseignez{" "}
            <code className="rounded bg-surface/60 px-1 text-xs">
              data/objective.yaml
            </code>{" "}
            pour cibler une épreuve.
          </div>
        ) : (
          <div className="space-y-1 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="text-gray-100">
                {OBJECTIVE_LABELS[objective.type] || objective.type}
              </span>
              {objective.date && (
                <span className="pill bg-accent/15 text-accent">
                  {objective.date}
                  {remainingDays != null && remainingDays >= 0 && (
                    <span className="ml-1 text-muted">
                      · J-{remainingDays}
                    </span>
                  )}
                </span>
              )}
            </div>
            {(objective.distance_km || objective.elevation_m) && (
              <div className="text-xs text-muted">
                {objective.distance_km != null && (
                  <span>{objective.distance_km} km</span>
                )}
                {objective.distance_km != null &&
                  objective.elevation_m != null && <span> · </span>}
                {objective.elevation_m != null && (
                  <span>{objective.elevation_m} m D+</span>
                )}
              </div>
            )}
            {objective.target_ftp != null && (
              <div className="text-xs text-muted">
                FTP cible : {objective.target_ftp} W
              </div>
            )}
            {objective.notes && (
              <div className="text-xs italic text-muted">{objective.notes}</div>
            )}
          </div>
        )}
      </div>

      {!viewing && (
      <div className="card space-y-3">
        <h2 className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <ClipboardList className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Générer un plan
        </h2>
        <div className="flex rounded-lg overflow-hidden border border-white/10 text-xs">
          <button
            type="button"
            onClick={() => setMode("classic")}
            className={`flex-1 px-3 py-2 transition-colors ${
              mode === "classic"
                ? "bg-accent text-surface font-semibold"
                : "bg-white/[0.04] text-muted hover:bg-white/[0.08]"
            }`}
          >
            Périodisation classique
          </button>
          <button
            type="button"
            onClick={() => setMode("llm")}
            className={`flex-1 px-3 py-2 transition-colors ${
              mode === "llm"
                ? "bg-accent text-surface font-semibold"
                : "bg-white/[0.04] text-muted hover:bg-white/[0.08]"
            }`}
          >
            Coach IA (bêta)
          </button>
        </div>
        <p className="text-xs text-muted">
          {mode === "classic"
            ? "Cycle 3:1 déterministe + taper. Sortie rapide, structure prédictible."
            : "Le coach LLM compose chaque semaine, garde-fous physiologiques appliqués automatiquement. Plus lent (quelques secondes par semaine)."}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-muted">Séances / semaine</span>
            <input
              type="number"
              min={2}
              max={7}
              step={1}
              value={sessionsPerWeek}
              onChange={(e) =>
                setSessionsPerWeek(
                  Math.max(2, Math.min(7, parseInt(e.target.value || "4", 10))),
                )
              }
              className="input mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs text-muted">Focus (optionnel)</span>
            <input
              type="text"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="ex: seuil, sprint, montagne…"
              className="input mt-1"
            />
          </label>
        </div>
        <button
          onClick={generate}
          disabled={generating}
          className="btn-primary w-full"
        >
          {generating ? (
            mode === "llm" ? (
              `Génération IA…${llmStream.weeks.length > 0 ? ` (${llmStream.weeks.length} sem)` : ""}`
            ) : (
              "Génération…"
            )
          ) : (
            <span className="inline-flex items-center justify-center gap-2">
              {mode === "llm" ? (
                <>
                  <Bot className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  Lancer le coach IA
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  Générer un plan
                </>
              )}
            </span>
          )}
        </button>
      </div>
      )}

      {(generating && mode === "llm") || llmStream.weeks.length > 0 || llmStream.error ? (
        <div className="card space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
              <Bot className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
              Génération coach IA
            </h3>
            {llmStream.summary && (
              <span className="pill bg-emerald-500/15 text-emerald-300">
                {llmStream.summary.totalWorkouts} séances ·{" "}
                {llmStream.summary.llmWeeks} sem IA
                {llmStream.summary.fallbackWeeks > 0 &&
                  ` · ${llmStream.summary.fallbackWeeks} fallback`}
              </span>
            )}
          </div>
          {llmStream.ctlCurrent != null && (
            <div className="text-xs text-muted">
              Contexte : CTL {llmStream.ctlCurrent.toFixed(1)}
              {llmStream.targetDate && ` · objectif ${llmStream.targetDate}`}
            </div>
          )}
          {llmStream.error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
              {llmStream.error}
            </div>
          )}
          <ul className="space-y-1 text-xs">
            {llmStream.weeks.map((week) => (
              <li
                key={week.index}
                className="rounded bg-surface/40 px-2 py-1.5 space-y-1"
              >
                <div className="flex items-center justify-between">
                  <span className="text-gray-200">
                    Semaine {week.index + 1} ·{" "}
                    {week.workouts.length} séance{week.workouts.length > 1 ? "s" : ""}
                  </span>
                  <span
                    className={`pill ${
                      week.source === "llm"
                        ? "bg-accent/15 text-accent"
                        : "bg-yellow-500/15 text-yellow-300"
                    }`}
                    title={
                      week.source === "llm"
                        ? "Générée par le LLM"
                        : "Fallback déterministe (LLM indisponible ou réponse invalide)"
                    }
                  >
                    {week.source === "llm" ? "IA" : "fallback"}
                  </span>
                </div>
                {week.adjustments.length > 0 && (
                  <details>
                    <summary className="cursor-pointer text-muted">
                      {week.adjustments.length} ajustement{week.adjustments.length > 1 ? "s" : ""}
                    </summary>
                    <ul className="mt-1 ml-3 list-disc space-y-0.5 text-muted">
                      {week.adjustments.map((adj, i) => (
                        <li key={i}>{adj}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card space-y-3">
        <h2 className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Library className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Plans persistés
        </h2>
        {plans.length === 0 ? (
          <div className="text-sm text-muted">
            Aucun plan pour le moment. Générez-en un ci-dessus.
          </div>
        ) : (
          <>
            <label className="block">
              <span className="text-xs text-muted">Sélection</span>
              <select
                value={selectedId ?? ""}
                onChange={(e) =>
                  setSelectedId(e.target.value ? Number(e.target.value) : null)
                }
                className="input mt-1"
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    #{p.id} — {describePlan(p)}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex gap-2">
              <button
                onClick={downloadZip}
                disabled={downloading || selectedId == null}
                className="btn-primary flex-1"
              >
                {downloading ? (
                  "Téléchargement…"
                ) : (
                  <span className="inline-flex items-center justify-center gap-2">
                    <Download className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    ZIP (.fit)
                  </span>
                )}
              </button>
              <button
                onClick={downloadIcs}
                disabled={downloadingIcs || selectedId == null}
                className="btn-ghost flex-1"
                title="Importer dans Google Calendar, Apple Calendar ou Outlook"
              >
                {downloadingIcs ? (
                  "Téléchargement…"
                ) : (
                  <span className="inline-flex items-center justify-center gap-2">
                    <Calendar className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    Calendrier (.ics)
                  </span>
                )}
              </button>
              {!viewing && (
                <button
                  onClick={remove}
                  disabled={deleting || selectedId == null}
                  className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400 hover:bg-red-500/20 disabled:opacity-50"
                >
                  {deleting ? (
                    "…"
                  ) : (
                    <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  )}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {loadingDetail && (
        <div className="card text-sm text-muted">Chargement du plan…</div>
      )}

      {!loadingDetail && detail && <PlanCalendar workouts={detail.workouts} />}
    </div>
  );
}
