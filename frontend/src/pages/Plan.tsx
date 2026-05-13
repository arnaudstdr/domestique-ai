import { useEffect, useState } from "react";
import { api, ApiError, streamGarminPush } from "../api/client";
import type { Objective, PlanDetail, PlanSummary } from "../api/types";
import PlanCalendar from "../components/PlanCalendar";
import { useToast } from "../hooks/useToast";

interface PushResult {
  date: string;
  name: string;
  url?: string;
  error?: string;
  scheduled: boolean;
}

interface PushState {
  index: number;
  total: number;
  currentWorkout: { date: string; name: string } | null;
  results: PushResult[];
  summary: { uploaded: number; errors: number } | null;
  error: string | null;
}

const EMPTY_PUSH: PushState = {
  index: 0,
  total: 0,
  currentWorkout: null,
  results: [],
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
  const [pushing, setPushing] = useState(false);
  const [pushSchedule, setPushSchedule] = useState(true);
  const [pushState, setPushState] = useState<PushState>(EMPTY_PUSH);
  const { push } = useToast();

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

  async function pushGarmin(): Promise<void> {
    if (selectedId == null || pushing) return;
    setPushing(true);
    setPushState({ ...EMPTY_PUSH });
    try {
      await streamGarminPush(selectedId, pushSchedule, (ev) => {
        setPushState((prev) => {
          if (ev.type === "start") {
            return { ...EMPTY_PUSH, total: ev.total };
          }
          if (ev.type === "progress") {
            return {
              ...prev,
              index: ev.index,
              total: ev.total,
              currentWorkout: ev.workout,
            };
          }
          if (ev.type === "result") {
            return {
              ...prev,
              currentWorkout: null,
              results: [
                ...prev.results,
                {
                  date: ev.workout.date,
                  name: ev.workout.name,
                  url: ev.url,
                  error: ev.error,
                  scheduled: ev.scheduled,
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
              currentWorkout: null,
              summary: { uploaded: ev.uploaded, errors: ev.errors },
            };
          }
          return prev;
        });
      });
      push("Push Garmin terminé.", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur Garmin : ${msg}`, "error");
      setPushState((prev) => ({ ...prev, error: msg }));
    } finally {
      setPushing(false);
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

  const remainingDays = objective?.date ? daysUntil(objective.date) : null;

  return (
    <div className="space-y-4">
      <div className="card space-y-2">
        <h2 className="text-base font-medium">🎯 Objectif courant</h2>
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

      <div className="card space-y-3">
        <h2 className="text-base font-medium">📋 Générer un plan</h2>
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
          {generating ? "Génération…" : "✨ Générer un plan"}
        </button>
      </div>

      <div className="card space-y-3">
        <h2 className="text-base font-medium">📚 Plans persistés</h2>
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
                {downloading ? "Téléchargement…" : "📥 Télécharger ZIP"}
              </button>
              <button
                onClick={remove}
                disabled={deleting || selectedId == null}
                className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400 hover:bg-red-500/20 disabled:opacity-50"
              >
                {deleting ? "…" : "🗑️"}
              </button>
            </div>

            <div className="space-y-2 border-t border-white/5 pt-3">
              <label className="flex items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={pushSchedule}
                  onChange={(e) => setPushSchedule(e.target.checked)}
                  className="h-4 w-4 rounded border-white/20 bg-surface accent-accent"
                />
                Planifier sur le calendrier Garmin
              </label>
              <button
                onClick={pushGarmin}
                disabled={pushing || selectedId == null}
                className="btn-primary w-full"
              >
                {pushing
                  ? `Envoi en cours… (${pushState.index + (pushState.currentWorkout ? 0 : 1)}/${pushState.total || "?"})`
                  : "☁️ Pousser sur Garmin Connect"}
              </button>
            </div>
          </>
        )}
      </div>

      {(pushing ||
        pushState.results.length > 0 ||
        pushState.summary ||
        pushState.error) && (
        <div className="card space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-gray-200">
              ☁️ Push Garmin Connect
            </h3>
            {pushState.summary && (
              <span
                className={`pill ${pushState.summary.errors > 0 ? "bg-orange-500/15 text-orange-300" : "bg-green-500/15 text-green-400"}`}
              >
                {pushState.summary.uploaded} envoyées
                {pushState.summary.errors > 0
                  ? ` · ${pushState.summary.errors} erreur(s)`
                  : ""}
              </span>
            )}
          </div>

          {pushState.total > 0 && (
            <div className="space-y-1">
              <div className="h-2 w-full overflow-hidden rounded-full bg-surface/60">
                <div
                  className="h-full bg-accent transition-all"
                  style={{
                    width: `${Math.min(100, ((pushState.index + (pushState.currentWorkout ? 0 : 1)) / pushState.total) * 100)}%`,
                  }}
                />
              </div>
              <div className="text-xs text-muted">
                {pushState.currentWorkout
                  ? `Envoi : ${pushState.currentWorkout.date} — ${pushState.currentWorkout.name}`
                  : pushState.summary
                    ? "Terminé."
                    : `${pushState.results.length} / ${pushState.total} traitées`}
              </div>
            </div>
          )}

          {pushState.error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
              {pushState.error}
              {pushState.error.toLowerCase().includes("token") && (
                <div className="mt-1 text-muted">
                  Lance{" "}
                  <code className="rounded bg-surface/60 px-1">
                    python -m domestique_ai.export.garmin_connect
                  </code>{" "}
                  sur le serveur pour reseeder le token.
                </div>
              )}
            </div>
          )}

          {pushState.results.length > 0 && (
            <ul className="max-h-48 space-y-1 overflow-y-auto text-xs">
              {pushState.results.slice(-10).reverse().map((r, i) => (
                <li
                  key={`${r.date}-${i}`}
                  className="flex items-center justify-between rounded bg-surface/40 px-2 py-1"
                >
                  <span className="truncate text-gray-200">
                    {r.date} — {r.name}
                  </span>
                  {r.error ? (
                    <span className="ml-2 shrink-0 text-red-400">⚠</span>
                  ) : r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 shrink-0 text-accent hover:underline"
                    >
                      {r.scheduled ? "✓ planifié" : "✓ uploadé"}
                    </a>
                  ) : (
                    <span className="ml-2 shrink-0 text-muted">—</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {loadingDetail && (
        <div className="card text-sm text-muted">Chargement du plan…</div>
      )}

      {!loadingDetail && detail && <PlanCalendar workouts={detail.workouts} />}
    </div>
  );
}
