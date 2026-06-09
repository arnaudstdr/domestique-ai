import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CalendarPlus, ClipboardList, Dumbbell, Trash2 } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { PrescriptionKind, PrescriptionOut } from "../api/types";
import { useToast } from "../hooks/useToast";
import { useViewing } from "../hooks/useViewing";

const KINDS: { value: PrescriptionKind; label: string }[] = [
  { value: "recovery", label: "Récupération (Z1)" },
  { value: "endurance", label: "Endurance (Z2)" },
  { value: "tempo", label: "Tempo (Z3)" },
  { value: "intervals", label: "Intervalles (Z4)" },
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function Prescribe() {
  const viewing = useViewing();
  const navigate = useNavigate();

  // La page n'a de sens que lorsqu'un athlète est consulté.
  useEffect(() => {
    if (!viewing) navigate("/roster");
  }, [viewing, navigate]);

  if (!viewing) return null;

  return (
    <div className="stagger space-y-4">
      <header>
        <h2 className="flex items-center gap-2 font-display text-2xl font-extrabold tracking-tight text-gray-50">
          <Dumbbell className="h-6 w-6 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Prescrire
        </h2>
        <p className="text-xs text-muted">
          Séances pour <strong>{viewing.name || "cet athlète"}</strong>. Une séance
          ponctuelle prime sur le plan généré ce jour-là.
        </p>
      </header>
      <PrescribeSessionSection publicId={viewing.id} />
      <AssignPlanSection publicId={viewing.id} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Séance ponctuelle
// ---------------------------------------------------------------------------

function PrescribeSessionSection({ publicId }: { publicId: string }) {
  const [date, setDate] = useState(todayIso());
  const [kind, setKind] = useState<PrescriptionKind>("endurance");
  const [duration, setDuration] = useState(90);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [items, setItems] = useState<PrescriptionOut[] | null>(null);
  const { push } = useToast();

  function refresh() {
    api.roster
      .listPrescriptions(publicId)
      .then(setItems)
      .catch(() => setItems([]));
  }

  useEffect(refresh, [publicId]);

  async function submit() {
    setSaving(true);
    try {
      await api.roster.prescribe(publicId, {
        date,
        kind,
        duration_min: duration,
        notes: notes.trim() || undefined,
      });
      push("Séance prescrite.", "success");
      setNotes("");
      refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Prescription : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  async function remove(pid: number) {
    try {
      await api.roster.deletePrescription(publicId, pid);
      refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Suppression : ${msg}`, "error");
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <CalendarPlus className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Séance ponctuelle
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Date</span>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Durée (min)</span>
          <input
            type="number"
            min={20}
            max={600}
            step={5}
            value={duration}
            onChange={(e) =>
              setDuration(Math.max(20, Math.min(600, parseInt(e.target.value || "60", 10))))
            }
            className="input mt-1"
          />
        </label>
      </div>
      <label className="block">
        <span className="text-xs text-muted">Type</span>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as PrescriptionKind)}
          className="input mt-1"
        >
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs text-muted">Notes (optionnel)</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Consignes pour l'athlète…"
          className="input mt-1"
        />
      </label>
      <button onClick={submit} disabled={saving} className="btn-primary w-full">
        {saving ? "Enregistrement…" : "Prescrire la séance"}
      </button>

      {items && items.length > 0 && (
        <ul className="space-y-2 pt-1">
          {items.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2.5"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-gray-100">{p.workout.name}</p>
                <p className="text-[11px] text-muted">
                  {p.date} · {p.workout.duration_min}′ · {p.workout.target_zone.toUpperCase()}
                </p>
                {p.workout.notes && (
                  <p className="truncate text-[11px] italic text-muted">{p.workout.notes}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => remove(p.id)}
                aria-label="Supprimer"
                className="shrink-0 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-400 hover:bg-red-500/20"
              >
                <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Assignation d'un plan complet
// ---------------------------------------------------------------------------

function AssignPlanSection({ publicId }: { publicId: string }) {
  const [sessions, setSessions] = useState(4);
  const [focus, setFocus] = useState("");
  const [assigning, setAssigning] = useState(false);
  const { push } = useToast();

  async function assign() {
    setAssigning(true);
    try {
      const plan = await api.roster.assignPlan(publicId, {
        sessions_per_week: sessions,
        focus: focus.trim() || null,
      });
      push(`Plan assigné (${plan.workouts.length} séances).`, "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Plan : ${msg}`, "error");
    } finally {
      setAssigning(false);
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <ClipboardList className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Assigner un plan complet
      </h3>
      <p className="text-xs text-muted">
        Génère un plan périodisé (3:1 + taper) à partir de l'objectif et de la
        disponibilité de l'athlète, et l'écrit dans son espace.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Séances / semaine</span>
          <input
            type="number"
            min={2}
            max={7}
            step={1}
            value={sessions}
            onChange={(e) =>
              setSessions(Math.max(2, Math.min(7, parseInt(e.target.value || "4", 10))))
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
            placeholder="ex: seuil, montagne…"
            className="input mt-1"
          />
        </label>
      </div>
      <button onClick={assign} disabled={assigning} className="btn-ghost w-full">
        {assigning ? "Génération…" : "Assigner un plan"}
      </button>
    </section>
  );
}
