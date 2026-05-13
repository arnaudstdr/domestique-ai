import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Availability,
  DayAvailability,
  Objective,
  Profile,
  WeekdayName,
} from "../api/types";
import { useToast } from "../hooks/useToast";

const WEEKDAYS: { key: WeekdayName; label: string }[] = [
  { key: "monday", label: "Lundi" },
  { key: "tuesday", label: "Mardi" },
  { key: "wednesday", label: "Mercredi" },
  { key: "thursday", label: "Jeudi" },
  { key: "friday", label: "Vendredi" },
  { key: "saturday", label: "Samedi" },
  { key: "sunday", label: "Dimanche" },
];

const OBJECTIVE_TYPES: { value: Objective["type"]; label: string }[] = [
  { value: "cyclosportive", label: "Cyclosportive" },
  { value: "course", label: "Course" },
  { value: "cyclo", label: "Cyclo (loisir)" },
  { value: "maintenance", label: "Maintenance" },
];

const EMPTY_OBJECTIVE: Objective = {
  type: "maintenance",
  date: null,
  distance_km: null,
  elevation_m: null,
  target_ftp: null,
  target_avg_hr_zone: null,
  notes: "",
};

const EMPTY_PROFILE: Profile = {
  ftp: null,
  hr_rest: null,
  hr_max: null,
  sex: "M",
  lthr_pct: 0.88,
};

export default function Profil() {
  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-base font-medium">⚙️ Profil</h2>
        <p className="text-xs text-muted">
          Trois sections pilotent l'app : tes paramètres physiologiques, ton
          objectif courant et ta disponibilité hebdomadaire. Chacune se
          sauvegarde indépendamment.
        </p>
      </header>
      <ProfileSection />
      <ObjectiveSection />
      <AvailabilitySection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Infos perso
// ---------------------------------------------------------------------------

function ProfileSection() {
  const [form, setForm] = useState<Profile>(EMPTY_PROFILE);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    api.profile
      .get()
      .then((p) => {
        if (p) setForm(p);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  function update<K extends keyof Profile>(key: K, value: Profile[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function parseNumber(value: string): number | null {
    if (!value.trim()) return null;
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  async function submit() {
    setSaving(true);
    try {
      const previous = await api.profile.get().catch(() => null);
      const saved = await api.profile.put(form);
      setForm(saved);
      const hrChanged =
        !previous ||
        previous.hr_rest !== saved.hr_rest ||
        previous.hr_max !== saved.hr_max ||
        previous.sex !== saved.sex ||
        previous.lthr_pct !== saved.lthr_pct;
      push(
        hrChanged
          ? "Profil enregistré. Recalcul de la charge en cours…"
          : "Profil enregistré.",
        "success",
      );
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Profil : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="text-sm font-medium text-gray-200">
        🧬 Infos perso
      </h3>
      <p className="text-xs text-muted">
        Pilote le calcul de charge (hr-TSS / TSS power) et les zones HR.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">FTP (W)</span>
          <input
            type="number"
            inputMode="numeric"
            value={form.ftp ?? ""}
            onChange={(e) => update("ftp", parseNumber(e.target.value))}
            placeholder="ex : 250"
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">Sexe</span>
          <select
            value={form.sex}
            onChange={(e) =>
              update("sex", e.target.value === "F" ? "F" : "M")
            }
            className="input mt-1"
          >
            <option value="M">M</option>
            <option value="F">F</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-muted">FC repos (bpm)</span>
          <input
            type="number"
            inputMode="numeric"
            value={form.hr_rest ?? ""}
            onChange={(e) => update("hr_rest", parseNumber(e.target.value))}
            placeholder="ex : 50"
            className="input mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">FC max (bpm)</span>
          <input
            type="number"
            inputMode="numeric"
            value={form.hr_max ?? ""}
            onChange={(e) => update("hr_max", parseNumber(e.target.value))}
            placeholder="ex : 190"
            className="input mt-1"
          />
        </label>
        <label className="col-span-2 block">
          <span className="text-xs text-muted">
            % LTHR ({(form.lthr_pct * 100).toFixed(0)} % HRR)
          </span>
          <input
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={form.lthr_pct}
            onChange={(e) =>
              update("lthr_pct", Number(e.target.value))
            }
            className="mt-2 w-full accent-accent"
          />
        </label>
      </div>
      <button
        onClick={submit}
        disabled={saving || !loaded}
        className="btn-primary w-full"
      >
        {saving ? "Enregistrement…" : "💾 Enregistrer le profil"}
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 2. Objectif
// ---------------------------------------------------------------------------

function ObjectiveSection() {
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
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Objectif : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="text-sm font-medium text-gray-200">🎯 Objectif</h3>
      <p className="text-xs text-muted">
        Lu par le coach et par le générateur de plan.
      </p>
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
      <button
        onClick={submit}
        disabled={saving || !loaded}
        className="btn-primary w-full"
      >
        {saving ? "Enregistrement…" : "💾 Enregistrer l'objectif"}
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 3. Disponibilité hebdo
// ---------------------------------------------------------------------------

interface DayFormState {
  enabled: boolean;
  max_duration_min: number;
  context: "indoor" | "outdoor";
}

const DEFAULT_DAY: DayFormState = {
  enabled: false,
  max_duration_min: 60,
  context: "outdoor",
};

function buildDefaultDays(): Record<WeekdayName, DayFormState> {
  return WEEKDAYS.reduce(
    (acc, day) => {
      acc[day.key] = { ...DEFAULT_DAY };
      return acc;
    },
    {} as Record<WeekdayName, DayFormState>,
  );
}

function AvailabilitySection() {
  const [days, setDays] = useState<Record<WeekdayName, DayFormState>>(
    buildDefaultDays,
  );
  const [longEnduranceDay, setLongEnduranceDay] = useState<WeekdayName | "">(
    "",
  );
  const [intervalsDay, setIntervalsDay] = useState<WeekdayName | "">("");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    api.availability
      .get()
      .then((av) => {
        if (av) {
          const base = buildDefaultDays();
          for (const wd of WEEKDAYS) {
            const day = av.days[wd.key];
            if (day) {
              base[wd.key] = {
                enabled: true,
                max_duration_min: day.max_duration_min,
                context: day.context,
              };
            }
          }
          setDays(base);
          if (av.preferences) {
            setLongEnduranceDay(av.preferences.long_endurance_day ?? "");
            setIntervalsDay(av.preferences.intervals_day ?? "");
          }
        }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  function updateDay<K extends keyof DayFormState>(
    key: WeekdayName,
    field: K,
    value: DayFormState[K],
  ) {
    setDays((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
  }

  async function submit() {
    setSaving(true);
    try {
      const enabledDays: Partial<Record<WeekdayName, DayAvailability>> = {};
      for (const wd of WEEKDAYS) {
        const day = days[wd.key];
        if (day.enabled) {
          enabledDays[wd.key] = {
            max_duration_min: day.max_duration_min,
            context: day.context,
          };
        }
      }
      const payload: Availability = {
        days: enabledDays,
        preferences:
          longEnduranceDay || intervalsDay
            ? {
                long_endurance_day: longEnduranceDay || null,
                intervals_day: intervalsDay || null,
              }
            : null,
      };
      const saved = await api.availability.put(payload);
      // Resync depuis le payload normalisé.
      const base = buildDefaultDays();
      for (const wd of WEEKDAYS) {
        const day = saved.days[wd.key];
        if (day) {
          base[wd.key] = {
            enabled: true,
            max_duration_min: day.max_duration_min,
            context: day.context,
          };
        }
      }
      setDays(base);
      setLongEnduranceDay(saved.preferences?.long_endurance_day ?? "");
      setIntervalsDay(saved.preferences?.intervals_day ?? "");
      push("Disponibilité enregistrée.", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Disponibilité : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  const enabledKeys = WEEKDAYS.filter((wd) => days[wd.key].enabled).map(
    (wd) => wd.key,
  );

  return (
    <section className="card space-y-3">
      <h3 className="text-sm font-medium text-gray-200">
        📅 Disponibilité hebdo
      </h3>
      <p className="text-xs text-muted">
        Coche les jours où tu peux t'entraîner. Le générateur de plan et la
        séance du jour respectent ces contraintes.
      </p>

      <div className="space-y-2">
        {WEEKDAYS.map((wd) => {
          const day = days[wd.key];
          return (
            <div
              key={wd.key}
              className="rounded-lg bg-surface/40 p-2 space-y-2"
            >
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={day.enabled}
                  onChange={(e) =>
                    updateDay(wd.key, "enabled", e.target.checked)
                  }
                  className="h-4 w-4 rounded border-white/20 bg-surface accent-accent"
                />
                <span className="font-medium text-gray-200">{wd.label}</span>
              </label>
              {day.enabled && (
                <div className="grid grid-cols-2 gap-2 pl-6">
                  <label className="block">
                    <span className="text-xs text-muted">Durée max (min)</span>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={20}
                      step={15}
                      value={day.max_duration_min}
                      onChange={(e) =>
                        updateDay(
                          wd.key,
                          "max_duration_min",
                          Math.max(20, Number(e.target.value) || 20),
                        )
                      }
                      className="input mt-1"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-muted">Contexte</span>
                    <select
                      value={day.context}
                      onChange={(e) =>
                        updateDay(
                          wd.key,
                          "context",
                          e.target.value === "indoor" ? "indoor" : "outdoor",
                        )
                      }
                      className="input mt-1"
                    >
                      <option value="indoor">Indoor</option>
                      <option value="outdoor">Outdoor</option>
                    </select>
                  </label>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-3 border-t border-white/5 pt-3">
        <label className="block">
          <span className="text-xs text-muted">
            Jour endurance longue (préférence)
          </span>
          <select
            value={longEnduranceDay}
            onChange={(e) =>
              setLongEnduranceDay((e.target.value as WeekdayName) || "")
            }
            className="input mt-1"
          >
            <option value="">— Auto —</option>
            {WEEKDAYS.filter((wd) => enabledKeys.includes(wd.key)).map((wd) => (
              <option key={wd.key} value={wd.key}>
                {wd.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-muted">
            Jour intervalles (préférence)
          </span>
          <select
            value={intervalsDay}
            onChange={(e) =>
              setIntervalsDay((e.target.value as WeekdayName) || "")
            }
            className="input mt-1"
          >
            <option value="">— Auto —</option>
            {WEEKDAYS.filter((wd) => enabledKeys.includes(wd.key)).map((wd) => (
              <option key={wd.key} value={wd.key}>
                {wd.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        onClick={submit}
        disabled={saving || !loaded}
        className="btn-primary w-full"
      >
        {saving ? "Enregistrement…" : "💾 Enregistrer la disponibilité"}
      </button>
    </section>
  );
}
