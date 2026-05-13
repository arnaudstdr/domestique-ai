import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Objective, PlanDetail, PlanSummary } from "../api/types";
import PlanCalendar from "../components/PlanCalendar";
import { useToast } from "../hooks/useToast";

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
