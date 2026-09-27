import { useEffect, useState } from "react";
import { Save, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Objective } from "../api/types";
import { useToast } from "../hooks/useToast";

const OBJECTIVE_TYPES: { value: Objective["type"]; label: string }[] = [
  { value: "cyclosportive", label: "Cyclosportive" },
  { value: "course", label: "Course" },
  { value: "cyclo", label: "Cyclo (loisir)" },
  { value: "forme", label: "Retour en forme / base" },
  { value: "maintenance", label: "Maintenance" },
];

const OBJECTIVE_TYPE_HINTS: Record<Objective["type"], string> = {
  cyclosportive: "Épreuve sportive : volume + intensité, décharge les 2 dernières semaines.",
  course: "Objectif de performance : intervalles chaque semaine, décharge les 2 dernières semaines.",
  cyclo: "Épreuve de longue distance : le volume passe avant tout, décharge courte.",
  forme: "Retrouver un niveau sans échéance de course : volume progressif, intervalles une semaine sur deux, pas de décharge finale.",
  maintenance: "Entretenir la forme : volume régulier et modéré, ni pic ni décharge.",
};

const EMPTY_OBJECTIVE: Objective = {
  type: "maintenance",
  date: null,
  distance_km: null,
  elevation_m: null,
  target_ftp: null,
  target_avg_hr_zone: null,
  notes: "",
};

interface Props {
  onSaved?: (objective: Objective) => void;
  onCancel?: () => void;
}

export default function ObjectiveForm({ onSaved, onCancel }: Props) {
  const [form, setForm] = useState<Objective>(EMPTY_OBJECTIVE);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    api.objective
      .get()
      .then((o) => {
        if (o) setForm({ ...EMPTY_OBJECTIVE, ...o });
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  function update<K extends keyof Objective>(key: K, value: Objective[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function parseNumber(value: string): number | null {
    if (!value.trim()) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  async function submit() {
    setSaving(true);
    try {
      const saved = await api.objective.put(form);
      setForm({ ...EMPTY_OBJECTIVE, ...saved });
      push("Objectif enregistré.", "success");
      onSaved?.(saved);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Objectif : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Type</span>
          <select
            value={form.type}
            onChange={(e) =>
              update("type", e.target.value as Objective["type"])
            }
            className="input mt-1"
          >
            {OBJECTIVE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[11px] leading-snug text-muted">
            {OBJECTIVE_TYPE_HINTS[form.type]}
          </span>
        </label>
        <label className="block">
          <span className="text-xs text-muted">Date cible</span>
          <input
            type="date"
            value={form.date ?? ""}
            onChange={(e) => update("date", e.target.value || null)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Distance (km)</span>
          <input
            type="number"
            inputMode="decimal"
            step={1}
            value={form.distance_km ?? ""}
            onChange={(e) =>
              update("distance_km", parseNumber(e.target.value))
            }
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Dénivelé (m)</span>
          <input
            type="number"
            inputMode="numeric"
            step={50}
            value={form.elevation_m ?? ""}
            onChange={(e) =>
              update("elevation_m", parseNumber(e.target.value))
            }
            className="input mt-1"
          />
        </label>
        <label className="col-span-2 block">
          <span className="text-xs text-muted">FTP cible (optionnel)</span>
          <input
            type="number"
            inputMode="numeric"
            value={form.target_ftp ?? ""}
            onChange={(e) =>
              update("target_ftp", parseNumber(e.target.value))
            }
            placeholder="ex : 280"
            className="input mt-1"
          />
        </label>
        <label className="col-span-2 block">
          <span className="text-xs text-muted">Notes</span>
          <textarea
            value={form.notes ?? ""}
            onChange={(e) => update("notes", e.target.value)}
            rows={2}
            placeholder="ex : Marmotte, finir avec sourire."
            className="input mt-1 resize-none"
          />
        </label>
      </div>
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={saving || !loaded}
          className="btn-primary flex-1"
        >
          {saving ? (
            "Enregistrement…"
          ) : (
            <span className="inline-flex items-center justify-center gap-2">
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Enregistrer l'objectif
            </span>
          )}
        </button>
        {onCancel && (
          <button
            onClick={onCancel}
            disabled={saving}
            className="btn-ghost px-4"
            type="button"
          >
            <span className="inline-flex items-center justify-center gap-2">
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Annuler
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
