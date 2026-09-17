import { useEffect, useState } from "react";
import { KeyRound, ShieldCheck } from "lucide-react";
import { ApiError, api } from "../api/client";
import TwoFactorSetup from "../components/TwoFactorSetup";

/**
 * Assistant d'activation de la 2FA, ouvert quand le middleware renvoie
 * `403 totp_setup_required` (ou après création de compte).
 *
 * Deux temps selon l'état du compte : définir email + mot de passe si absents
 * (sessions historiques), puis enrôler le TOTP.
 */
export default function SetupTwoFactor() {
  const [needsCredentials, setNeedsCredentials] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    api.auth
      .me()
      .then((me) => alive && setNeedsCredentials(!me.email))
      .catch(() => alive && setNeedsCredentials(true));
    return () => {
      alive = false;
    };
  }, []);

  function done() {
    const next = new URLSearchParams(window.location.search).get("next") || "/";
    window.location.assign(next);
  }

  async function submitCredentials(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.auth.setupCredentials(email.trim(), password);
      setNeedsCredentials(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(err.message);
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Mot de passe trop court (10 caractères minimum).");
      } else {
        setError("Échec de l'enregistrement des identifiants.");
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 py-8 text-gray-100">
      <div className="card w-full max-w-md space-y-4 p-6">
        {needsCredentials === null ? (
          <p className="text-center text-sm text-muted">Chargement…</p>
        ) : needsCredentials ? (
          <form onSubmit={submitCredentials} className="space-y-4">
            <div className="flex items-center gap-2 text-accent">
              <KeyRound className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              <h3 className="font-display text-lg font-extrabold">
                Définir tes identifiants
              </h3>
            </div>
            <p className="text-sm text-gray-300">
              Choisis un email et un mot de passe. La double authentification
              (2FA) sera activée à l'étape suivante.
            </p>
            <label className="block">
              <span className="text-xs text-gray-400">Email</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="input mt-1 w-full"
                placeholder="toi@exemple.com"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Mot de passe</span>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input mt-1 w-full"
                placeholder="10 caractères minimum"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Confirmer</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="input mt-1 w-full"
              />
            </label>
            {error ? (
              <p className="text-xs text-red-400" role="alert">
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={submitting || !email.trim() || password.length < 10}
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Enregistrement…" : "Continuer"}
            </button>
          </form>
        ) : (
          <>
            <div className="flex items-center justify-center gap-2 text-accent">
              <ShieldCheck className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              <span className="text-xs uppercase tracking-wide">
                Sécurisation du compte
              </span>
            </div>
            <TwoFactorSetup onDone={done} />
          </>
        )}
      </div>
    </div>
  );
}
