import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Pencil, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { ClimbSummary } from "../api/types";
import { useToast } from "../hooks/useToast";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, "0")}`;
  return `${minutes} min ${String(secs).padStart(2, "0")}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
}

function ClimbCard({
  climb,
  onRenamed,
}: {
  climb: ClimbSummary;
  onRenamed: (updated: ClimbSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(climb.name ?? "");
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  async function save() {
    setSaving(true);
    try {
      const updated = await api.climbs.rename(climb.id, draft.trim());
      onRenamed(updated);
      setEditing(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Impossible de nommer la montée : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        {editing ? (
          <div className="flex flex-1 items-center gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void save();
                }
                if (event.key === "Escape") setEditing(false);
              }}
              maxLength={80}
              placeholder="Nom de la montée (ex. Haut-Koenigsbourg)"
              className="input flex-1"
              autoFocus
            />
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="btn-primary"
            >
              {saving ? "…" : "OK"}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="btn-ghost"
              aria-label="Annuler"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <>
            <div>
              <h3 className="font-display text-lg font-bold text-fg">
                {climb.name || `Montée sans nom #${climb.id}`}
              </h3>
              <p className="text-xs text-muted">
                {climb.efforts} passage{climb.efforts > 1 ? "s" : ""} · {climb.length_m / 1000} km
                · {climb.gain_m} m D+ · {climb.avg_gradient_pct} % moyen
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setDraft(climb.name ?? "");
                setEditing(true);
              }}
              className="btn-ghost"
              aria-label="Nommer la montée"
              title="Nommer la montée"
            >
              <Pencil className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="flex flex-col rounded-xl border border-border/[0.08] bg-overlay/[0.03] px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-muted">Meilleur</span>
          <span className="font-display text-lg font-bold text-fg">
            {formatDuration(climb.best_sec)}
          </span>
        </div>
        <div className="flex flex-col rounded-xl border border-border/[0.08] bg-overlay/[0.03] px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-muted">Moyenne</span>
          <span className="font-display text-lg font-bold text-fg">
            {formatDuration(climb.avg_sec)}
          </span>
        </div>
        <div className="flex flex-col rounded-xl border border-border/[0.08] bg-overlay/[0.03] px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-muted">VAM max</span>
          <span className="font-display text-lg font-bold text-fg">
            {climb.best_vam_m_h ? `${Math.round(climb.best_vam_m_h)} m/h` : "—"}
          </span>
        </div>
        <div className="flex flex-col rounded-xl border border-border/[0.08] bg-overlay/[0.03] px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-muted">Dernier</span>
          <span className="font-display text-lg font-bold text-fg">
            {formatDate(climb.last_date)}
          </span>
        </div>
      </div>

      {climb.by_year.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {climb.by_year.map((year) => (
            <span
              key={year.year}
              className="rounded-full border border-border/[0.1] bg-overlay/[0.04] px-2.5 py-1 text-xs text-fg-soft"
            >
              {year.year} · {year.efforts}× · {formatDuration(year.best_sec)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Climbs() {
  const [climbs, setClimbs] = useState<ClimbSummary[] | null>(null);
  const { push } = useToast();

  const load = useCallback(async () => {
    try {
      const response = await api.climbs.list();
      setClimbs(response.climbs);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur de chargement : ${msg}`, "error");
      setClimbs([]);
    }
  }, [push]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stagger space-y-4">
      <div>
        <h2 className="font-display text-2xl font-extrabold tracking-tight text-fg">Montées</h2>
        <p className="mt-1 text-sm text-muted">
          Montées détectées automatiquement dans tes sorties (départ/arrivée proches). Nomme-les
          pour que le coach réponde « combien de fois », « meilleur temps », etc.
        </p>
      </div>

      {climbs === null && <div className="card text-sm text-muted">Chargement…</div>}

      {climbs?.length === 0 && (
        <div className="card space-y-2 text-sm text-muted">
          <p>Aucune montée détectée pour l'instant.</p>
          <p>
            La détection utilise les streams persistés (altitude/temps). L'historique Garmin se
            remplit via le backfill :{" "}
            <code className="rounded bg-overlay/[0.06] px-1.5 py-0.5 text-xs">
              python -m domestique_ai.ingestion.backfill_streams --all
            </code>
          </p>
        </div>
      )}

      {climbs?.map((climb) => (
        <ClimbCard
          key={climb.id}
          climb={climb}
          onRenamed={(updated) =>
            setClimbs((prev) =>
              (prev ?? []).map((item) => (item.id === updated.id ? updated : item)),
            )
          }
        />
      ))}

      <Link to="/tendances" className="inline-block text-xs font-semibold text-accent hover:underline">
        ← Retour aux tendances
      </Link>
    </div>
  );
}
