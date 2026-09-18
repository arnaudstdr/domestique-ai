import { useState } from "react";
import { Save, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import { useToast } from "../hooks/useToast";
import { SPORTS } from "./sports";

interface Props {
  onCreated: () => void;
  onClose: () => void;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ActivityCreateForm({ onCreated, onClose }: Props) {
  const [date, setDate] = useState(todayIso);
  const [time, setTime] = useState("08:00");
  const [sport, setSport] = useState("Ride");
  const [durationMin, setDurationMin] = useState("");
  const [distanceKm, setDistanceKm] = useState("");
  const [elevationM, setElevationM] = useState("");
  const [avgHr, setAvgHr] = useState("");
  const [avgPower, setAvgPower] = useState("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  const num = (v: string): number | null => (v.trim() === "" ? null : Number(v));
  const durationSec = Math.round((num(durationMin) ?? 0) * 60);
  const canSubmit = date !== "" && durationSec > 0 && !saving;

  async function submit() {
    if (!canSubmit) return;
    setSaving(true);
    try {
      const iso = new Date(`${date}T${time || "00:00"}:00`).toISOString();
      await api.activities.create({
        date: iso,
        sport_type: sport,
        duration_sec: durationSec,
        distance_km: num(distanceKm) ?? 0,
        elevation_m: num(elevationM),
        avg_hr: num(avgHr),
        max_hr: null,
        avg_power: num(avgPower),
        name: name.trim() || null,
      });
      push("Activité ajoutée.", "success");
      onCreated();
      onClose();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm font-bold text-gray-50">
          Ajouter une activité
        </h3>
        <button onClick={onClose} className="btn-ghost !px-2 !py-1" aria-label="Fermer">
          <X size={16} />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Date</span>
          <input
            type="date"
            value={date}
            max={todayIso()}
            onChange={(e) => setDate(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Heure</span>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="input mt-1"
          />
        </label>
      </div>

      <label className="block">
        <span className="text-xs text-muted">Sport</span>
        <select
          value={sport}
          onChange={(e) => setSport(e.target.value)}
          className="input mt-1"
        >
          {SPORTS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Durée (min)</span>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            value={durationMin}
            onChange={(e) => setDurationMin(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Distance (km)</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            step="0.1"
            value={distanceKm}
            onChange={(e) => setDistanceKm(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">D+ (m)</span>
          <input
            type="number"
            inputMode="numeric"
            min={0}
            value={elevationM}
            onChange={(e) => setElevationM(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">FC moyenne (bpm)</span>
          <input
            type="number"
            inputMode="numeric"
            min={0}
            value={avgHr}
            onChange={(e) => setAvgHr(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Puissance moy. (W)</span>
          <input
            type="number"
            inputMode="numeric"
            min={0}
            value={avgPower}
            onChange={(e) => setAvgPower(e.target.value)}
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Nom</span>
          <input
            type="text"
            value={name}
            placeholder="Séance manuelle"
            onChange={(e) => setName(e.target.value)}
            className="input mt-1"
          />
        </label>
      </div>

      <button
        onClick={submit}
        disabled={!canSubmit}
        className="btn-primary w-full disabled:opacity-50"
      >
        <span className="inline-flex items-center justify-center gap-2">
          <Save size={16} />
          {saving ? "Enregistrement…" : "Enregistrer"}
        </span>
      </button>
    </div>
  );
}
