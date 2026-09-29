import { useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  Camera,
  Dna,
  Link2,
  LogOut,
  Save,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import { api, ApiError, clearApiToken } from "../api/client";
import type {
  Availability,
  DayAvailability,
  GarminStatus,
  MeResponse,
  Profile,
  WeekdayName,
} from "../api/types";
import CalendarSubscribe from "../components/CalendarSubscribe";
import MemoryPanel from "../components/MemoryPanel";
import { useToast } from "../hooks/useToast";
import { useMe, useMeRefresh } from "../hooks/useMe";
import { useViewing } from "../hooks/useViewing";
import { resizeImageToSquare } from "../lib/image";

const WEEKDAYS: { key: WeekdayName; label: string }[] = [
  { key: "monday", label: "Lundi" },
  { key: "tuesday", label: "Mardi" },
  { key: "wednesday", label: "Mercredi" },
  { key: "thursday", label: "Jeudi" },
  { key: "friday", label: "Vendredi" },
  { key: "saturday", label: "Samedi" },
  { key: "sunday", label: "Dimanche" },
];

const LEVELS: { value: Profile["level"]; label: string }[] = [
  { value: "beginner", label: "Débutant" },
  { value: "intermediate", label: "Intermédiaire" },
  { value: "advanced", label: "Avancé" },
  { value: "ex_competitor", label: "Ancien compétiteur (reprise)" },
];

const LEVEL_HINTS: Record<Profile["level"], string> = {
  beginner: "Peu d'historique d'entraînement : progression lente, base d'abord.",
  intermediate: "Base correcte : le coach équilibre volume et intensité.",
  advanced: "Entraîné régulier : l'intensité peut être soutenue rapidement.",
  ex_competitor:
    "Historique de compétiteur qui reprend : la caisse revient vite, mais le coach reste prudent sur les charges.",
};

const EMPTY_PROFILE: Profile = {
  ftp: null,
  hr_rest: null,
  hr_max: null,
  sex: "M",
  lthr_pct: 0.88,
  level: "intermediate",
};

export default function Profil() {
  return (
    <div className="stagger space-y-4">
      <header>
        <h2 className="flex items-center gap-2 font-display text-2xl font-extrabold tracking-tight text-gray-50">
          <UserRound className="h-6 w-6 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Profil
        </h2>
        <p className="text-xs text-muted">
          Ces sections pilotent l'app : tes paramètres physiologiques et ta
          disponibilité hebdomadaire. Chacune se sauvegarde indépendamment.
        </p>
      </header>
      <AvatarSection />
      <ProfileSection />
      <AvailabilitySection />
      <GarminSection />
      <CalendarSubscribe />
      <MemoryPanel />
      <SecuritySection />
      <AccountSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 0. Photo de profil
// ---------------------------------------------------------------------------

function AvatarSection() {
  const me = useMe();
  const refreshMe = useMeRefresh();
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { push } = useToast();

  async function onFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Réinitialise l'input pour permettre de re-choisir le même fichier.
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const blob = await resizeImageToSquare(file);
      const upload = new File([blob], "avatar.jpg", { type: "image/jpeg" });
      await api.auth.uploadAvatar(upload);
      refreshMe();
      push("Photo de profil mise à jour.", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Photo : ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.auth.removeAvatar();
      refreshMe();
      push("Photo de profil supprimée.", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Photo : ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Camera className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Photo de profil
      </h3>
      <div className="flex items-center gap-4">
        <div className="grid h-16 w-16 shrink-0 place-items-center overflow-hidden rounded-2xl border border-white/10 bg-white/[0.04]">
          {me?.avatar_url ? (
            <img
              src={me.avatar_url}
              alt="Photo de profil"
              className="h-full w-full object-cover"
            />
          ) : (
            <UserRound className="h-7 w-7 text-muted" strokeWidth={1.5} aria-hidden="true" />
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={onFile}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
            className="btn-ghost w-full"
          >
            {busy ? "Traitement…" : me?.avatar_url ? "Changer la photo" : "Ajouter une photo"}
          </button>
          {me?.avatar_url && (
            <button
              type="button"
              onClick={remove}
              disabled={busy}
              className="btn-ghost w-full text-red-400"
            >
              <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Retirer la photo
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted">
        L'image est recadrée en carré et redimensionnée (256 px) avant l'envoi.
        Elle remplace l'icône profil en haut à droite.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 0. Sync Garmin Connect
// ---------------------------------------------------------------------------

function GarminSection() {
  const [status, setStatus] = useState<GarminStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPending, setMfaPending] = useState(false);
  const [reauth, setReauth] = useState(false);
  const [busy, setBusy] = useState(false);
  const { push } = useToast();
  const viewing = useViewing();

  function load() {
    api.garmin
      .status()
      .then((s) => {
        setStatus(s);
        if (s.email) setEmail(s.email);
      })
      .catch(() => setStatus(null));
  }

  useEffect(() => {
    load();
  }, []);

  async function sync() {
    setSyncing(true);
    try {
      const res = await api.garmin.sync();
      push(
        res.status === "syncing"
          ? "Synchronisation Garmin lancée."
          : `Sync Garmin : ${res.inserted ?? 0} activité(s) importée(s).`,
        "success",
      );
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Garmin : ${msg}`, "error");
    } finally {
      setSyncing(false);
      load();
    }
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const res = await api.garmin.connect(email.trim(), password);
      if (res.status === "mfa_required") {
        setMfaPending(true);
        push("Code MFA envoyé par Garmin — saisis-le ci-dessous.", "info");
      } else {
        setPassword("");
        setReauth(false);
        push("Garmin Connect connecté.", "success");
      }
      load();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Connexion Garmin : ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitMfa(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.garmin.submitMfa(mfaCode.trim());
      setMfaPending(false);
      setMfaCode("");
      setPassword("");
      setReauth(false);
      push("Code MFA validé — Garmin Connect connecté.", "success");
      load();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Validation MFA : ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.garmin.disconnect();
      setEmail("");
      setPassword("");
      push("Garmin Connect déconnecté.", "success");
      load();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Déconnexion Garmin : ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  const connected = status?.connected ?? false;

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Link2 className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Garmin Connect
      </h3>
      <p className="text-xs text-muted">
        Connecte ton compte Garmin Connect personnel (compteur Edge / montre) —
        chaque athlète a sa propre connexion, isolée des autres.
      </p>

      {status === null ? (
        <p className="text-xs text-muted">Vérification…</p>
      ) : viewing ? (
        <p className="text-xs text-muted">
          Consultation en lecture seule — la connexion Garmin appartient à
          l'athlète consulté.
        </p>
      ) : status.needs_reauth && status.connected && !reauth ? (
        <div className="space-y-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3">
          <p className="text-xs text-red-300">
            Connexion Garmin expirée ou rejetée — reconnecte-toi pour reprendre la
            synchronisation.
          </p>
          <button
            type="button"
            onClick={() => {
              setMfaPending(false);
              setPassword("");
              setReauth(true);
            }}
            className="btn-primary w-full"
          >
            Reconnecter Garmin
          </button>
        </div>
      ) : connected && !reauth ? (
        <>
          {status.orphan_tokens && (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-300">
              Anciens tokens Garmin globaux détectés — une reconnexion est
              recommandée pour garantir l'isolation du compte.
            </p>
          )}
          <p className="text-sm text-accent">
            Garmin Connect connecté{status.email ? ` (${status.email})` : ""} —
            auto-sync activée.
          </p>
          <button
            type="button"
            onClick={sync}
            disabled={syncing || status.sync.status === "syncing"}
            className="btn-primary w-full"
          >
            {syncing || status.sync.status === "syncing"
              ? "Synchronisation…"
              : "Synchroniser maintenant"}
          </button>
          {status.sync.status === "error" && status.sync.error && (
            <p className="text-xs text-red-400" role="alert">
              {status.sync.error}
            </p>
          )}
          <button
            type="button"
            onClick={disconnect}
            disabled={busy}
            className="btn-ghost w-full"
          >
            Déconnecter Garmin
          </button>
        </>
      ) : mfaPending ? (
        <form onSubmit={submitMfa} className="space-y-2">
          <p className="text-xs text-muted">
            Garmin a envoyé un code de vérification (email/SMS). Saisis-le pour
            terminer la connexion.
          </p>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={mfaCode}
            onChange={(e) => setMfaCode(e.target.value)}
            placeholder="Code MFA"
            className="w-full rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"
            required
          />
          <div className="flex gap-2">
            <button type="submit" disabled={busy} className="btn-primary flex-1">
              {busy ? "Validation…" : "Valider le code"}
            </button>
            <button
              type="button"
              onClick={() => {
                setMfaPending(false);
                setMfaCode("");
              }}
              className="btn-ghost"
            >
              Annuler
            </button>
          </div>
        </form>
      ) : (
        <form onSubmit={connect} className="space-y-2">
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email Garmin Connect"
            className="w-full rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"
            required
          />
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Mot de passe"
            className="w-full rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"
            required
          />
          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Connexion…" : "Connecter Garmin"}
          </button>
        </form>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 5. Sécurité (mot de passe + 2FA)
// ---------------------------------------------------------------------------

function SecuritySection() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [otpPassword, setOtpPassword] = useState("");
  const [otpBusy, setOtpBusy] = useState(false);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const { push } = useToast();

  useEffect(() => {
    api.auth.me().then(setMe).catch(() => setMe(null));
  }, []);

  async function changePassword(event: React.FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      push("Les deux mots de passe ne correspondent pas.", "error");
      return;
    }
    setPwBusy(true);
    try {
      await api.auth.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      push("Mot de passe modifié.", "success");
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 401
          ? "Mot de passe actuel incorrect."
          : err instanceof ApiError && err.status === 422
            ? "Nouveau mot de passe trop court (10 caractères minimum)."
            : "Échec du changement de mot de passe.";
      push(msg, "error");
    } finally {
      setPwBusy(false);
    }
  }

  async function regenerateCodes(event: React.FormEvent) {
    event.preventDefault();
    setOtpBusy(true);
    try {
      const res = await api.auth.regenerateRecoveryCodes(otpPassword);
      setNewCodes(res.recovery_codes);
      setOtpPassword("");
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 401
          ? "Mot de passe incorrect."
          : "Échec de la régénération.";
      push(msg, "error");
    } finally {
      setOtpBusy(false);
    }
  }

  async function disableTotp() {
    setOtpBusy(true);
    try {
      await api.auth.totpDisable(otpPassword);
      setOtpPassword("");
      push("2FA désactivée — réactive-la depuis l'assistant.", "success");
      window.location.assign("/setup-2fa");
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 401
          ? "Mot de passe incorrect."
          : "Échec de la désactivation.";
      push(msg, "error");
      setOtpBusy(false);
    }
  }

  return (
    <section className="card space-y-4">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <ShieldCheck className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Sécurité
      </h3>

      <div className="rounded-lg bg-surface/40 p-3 text-sm">
        {me === null ? (
          <span className="text-muted">—</span>
        ) : me.totp_enabled ? (
          <span className="inline-flex items-center gap-2 text-accent">
            <ShieldCheck className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Double authentification active ({me.email})
          </span>
        ) : (
          <span className="flex flex-wrap items-center gap-2 text-gray-300">
            Double authentification inactive.
            <a href="/setup-2fa" className="text-accent hover:underline">
              Activer la 2FA
            </a>
          </span>
        )}
      </div>

      <form onSubmit={changePassword} className="space-y-2">
        <p className="label-eyebrow">Changer le mot de passe</p>
        <input
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          placeholder="Mot de passe actuel"
          className="input w-full"
        />
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          placeholder="Nouveau mot de passe (10 car. min)"
          className="input w-full"
        />
        <input
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder="Confirmer le nouveau mot de passe"
          className="input w-full"
        />
        <button
          type="submit"
          disabled={pwBusy || !current || next.length < 10 || next !== confirm}
          className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pwBusy ? "Modification…" : "Modifier le mot de passe"}
        </button>
      </form>

      {me?.totp_enabled ? (
        <div className="space-y-2 border-t border-white/5 pt-3">
          <p className="label-eyebrow">Double authentification</p>
          {newCodes ? (
            <div className="space-y-2">
              <p className="text-xs text-gray-300">
                Nouveaux codes de secours (affichés une seule fois) :
              </p>
              <ul className="grid grid-cols-2 gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.06] p-3 font-mono text-xs">
                {newCodes.map((c) => (
                  <li key={c} className="tracking-wide text-gray-100">
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <form onSubmit={regenerateCodes} className="space-y-2">
              <input
                type="password"
                autoComplete="current-password"
                value={otpPassword}
                onChange={(e) => setOtpPassword(e.target.value)}
                placeholder="Mot de passe pour confirmer"
                className="input w-full"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={otpBusy || !otpPassword}
                  className="btn-ghost flex-1 disabled:opacity-50"
                >
                  Régénérer les codes
                </button>
                <button
                  type="button"
                  onClick={disableTotp}
                  disabled={otpBusy || !otpPassword}
                  className="btn-ghost flex-1 text-red-400 disabled:opacity-50"
                >
                  Désactiver la 2FA
                </button>
              </div>
            </form>
          )}
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 4. Compte
// ---------------------------------------------------------------------------

function AccountSection() {
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    api.auth.me().then(setMe).catch(() => setMe(null));
  }, []);

  async function logout() {
    try {
      await api.auth.logout();
    } catch {
      // best-effort : on déconnecte localement même si l'appel échoue.
    }
    clearApiToken();
    window.location.assign("/login");
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <UserRound className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Compte
      </h3>
      {me ? (
        <p className="text-sm text-gray-300">
          {me.display_name || "Sans nom"}{" "}
          <span className="text-muted">· {me.role}</span>
        </p>
      ) : (
        <p className="text-xs text-muted">—</p>
      )}
      <button
        type="button"
        onClick={logout}
        className="btn-ghost flex w-full items-center justify-center gap-2"
      >
        <LogOut className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        Se déconnecter
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 1. Infos perso
// ---------------------------------------------------------------------------

function ProfileSection() {
  const [form, setForm] = useState<Profile>(EMPTY_PROFILE);
  const [weight, setWeight] = useState<string>("");
  const [savedWeight, setSavedWeight] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    Promise.all([
      api.profile.get().catch(() => null),
      api.morning.weight().catch(() => null),
    ])
      .then(([p, w]) => {
        if (p) setForm(p);
        if (w?.weight_kg != null) {
          setWeight(w.weight_kg.toString());
          setSavedWeight(w.weight_kg);
        }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  const parsedWeight = Number(weight.replace(",", "."));
  const wkg =
    form.ftp && Number.isFinite(parsedWeight) && parsedWeight > 0
      ? form.ftp / parsedWeight
      : null;

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

      const weightChanged =
        Number.isFinite(parsedWeight) &&
        parsedWeight > 0 &&
        parsedWeight !== savedWeight;
      if (weightChanged) {
        const w = await api.morning.setWeight(parsedWeight);
        setSavedWeight(w.weight_kg);
      }

      push(
        hrChanged
          ? "Profil enregistré. Recalcul de la charge en cours…"
          : weightChanged
            ? "Profil et poids enregistrés."
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
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Dna className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Infos perso
      </h3>
      <p className="text-xs text-muted">
        Pilote le calcul de charge (hr-TSS / TSS power), les zones HR et le
        rapport poids/puissance (le poids est suivi sur la page Santé).
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
        <label className="block">
          <span className="text-xs text-muted">Poids (kg)</span>
          <input
            type="number"
            inputMode="decimal"
            step={0.1}
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            placeholder="ex : 70"
            className="input mt-1"
          />
        </label>
        <div className="block">
          <span className="text-xs text-muted">Rapport poids/puissance</span>
          <div className="mt-1 flex h-9 items-center rounded-lg border border-white/10 bg-white/5 px-3 text-sm text-gray-200">
            {wkg != null ? `${wkg.toFixed(2)} W/kg` : "—"}
          </div>
        </div>
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
        <label className="col-span-2 block">
          <span className="text-xs text-muted">Niveau / expérience</span>
          <select
            value={form.level}
            onChange={(e) =>
              update(
                "level",
                e.target.value as Profile["level"],
              )
            }
            className="input mt-1"
          >
            {LEVELS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[11px] text-muted">
            {LEVEL_HINTS[form.level] ??
              "Aide le coach à calibrer la reprise (volume, intensité)."}
          </span>
        </label>
      </div>
      <button
        onClick={submit}
        disabled={saving || !loaded}
        className="btn-primary w-full"
      >
        {saving ? (
          "Enregistrement…"
        ) : (
          <span className="inline-flex items-center justify-center gap-2">
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Enregistrer le profil
          </span>
        )}
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 2. Disponibilité hebdo
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
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <CalendarDays className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Disponibilité hebdo
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
        {saving ? (
          "Enregistrement…"
        ) : (
          <span className="inline-flex items-center justify-center gap-2">
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Enregistrer la disponibilité
          </span>
        )}
      </button>
    </section>
  );
}
