import { useState } from "react";
import { ApiError, api, setApiToken } from "../api/client";

/**
 * Connexion email + mot de passe, puis code TOTP (ou code de secours).
 *
 * Le token de session renvoyé est stocké comme Bearer en `localStorage`, puis
 * l'app redirige vers le chemin initial (query `?next=...`).
 */
export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function redirect() {
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next") || "/";
    // Reload complet pour repartir avec le nouveau header sur tous les fetchs.
    window.location.assign(next);
  }

  async function submitCredentials(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.login(email.trim(), password);
      if (res.status === "totp_required" && res.challenge) {
        setChallenge(res.challenge);
        setSubmitting(false);
        return;
      }
      if (res.session_token) {
        setApiToken(res.session_token);
        redirect();
        return;
      }
      setError("Réponse inattendue du serveur.");
      setSubmitting(false);
    } catch (err) {
      setError(loginErrorMessage(err));
      setSubmitting(false);
    }
  }

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    if (!challenge || !code.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.loginTotp(challenge, code.trim());
      if (res.session_token) {
        setApiToken(res.session_token);
        redirect();
        return;
      }
      setError("Réponse inattendue du serveur.");
      setSubmitting(false);
    } catch (err) {
      setError(actionErrorMessage(err, "Code de vérification incorrect."));
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 text-gray-100">
      <form
        onSubmit={challenge ? submitCode : submitCredentials}
        className="card w-full max-w-sm space-y-4 p-6"
      >
        <div className="text-center">
          <img
            src="/icon-192.png"
            alt=""
            aria-hidden="true"
            className="mx-auto h-16 w-16 rounded-2xl ring-1 ring-white/10 shadow-card"
          />
          <h1 className="mt-3 font-display text-xl font-extrabold tracking-tight">
            Domestique<span className="text-accent">AI</span>
          </h1>
          <p className="mt-1 text-xs text-gray-400">
            {challenge ? "Vérification en 2 étapes" : "Connexion"}
          </p>
        </div>

        {challenge ? (
          <>
            <p className="text-sm text-gray-300">
              {useRecovery
                ? "Saisis l'un de tes codes de secours."
                : "Saisis le code à 6 chiffres de ton application d'authentification."}
            </p>
            <label className="block">
              <span className="text-xs text-gray-400">
                {useRecovery ? "Code de secours" : "Code de vérification"}
              </span>
              <input
                autoFocus
                inputMode={useRecovery ? "text" : "numeric"}
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
                className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm tracking-widest focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
            <button
              type="button"
              onClick={() => {
                setUseRecovery((v) => !v);
                setCode("");
                setError(null);
              }}
              className="text-xs text-gray-400 hover:text-accent"
            >
              {useRecovery
                ? "Utiliser un code à 6 chiffres"
                : "Utiliser un code de secours"}
            </button>
          </>
        ) : (
          <>
            <label className="block">
              <span className="text-xs text-gray-400">Email</span>
              <input
                type="email"
                autoFocus
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="toi@exemple.com"
                className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Mot de passe</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••••••"
                className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
          </>
        )}

        {error ? (
          <p className="text-xs text-red-400" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting || (challenge ? !code.trim() : !email.trim() || !password)}
          className="w-full rounded-lg bg-accent py-2 text-sm font-semibold text-surface transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting
            ? "Connexion…"
            : challenge
              ? "Valider"
              : "Se connecter"}
        </button>
      </form>
    </div>
  );
}

function loginErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return "Trop de tentatives. Compte temporairement verrouillé, réessaie dans quelques minutes.";
    }
    if (err.status === 401) {
      return "Email ou mot de passe incorrect.";
    }
  }
  return "Échec de la connexion.";
}

function actionErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return "Trop de tentatives. Réessaie dans quelques minutes.";
    }
    if (err.status === 401) {
      return fallback;
    }
  }
  return fallback;
}
