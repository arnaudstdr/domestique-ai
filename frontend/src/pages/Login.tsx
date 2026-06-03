import { useState } from "react";
import { setApiToken } from "../api/client";

/**
 * Mini page de saisie du token API Bearer (CR-021).
 *
 * Affichée quand le backend renvoie 401 sur n'importe quelle requête. Le
 * token est stocké en `localStorage` puis l'app redirige vers le chemin
 * initial (passé en query `?next=...`).
 */
export default function Login() {
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      setApiToken(trimmed);
    } catch {
      setError(
        "Impossible de stocker le token (localStorage indisponible).",
      );
      setSubmitting(false);
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next") || "/";
    // On force un reload complet pour que les éventuels caches SSE/SW
    // soient repartis avec le nouveau header sur tous les fetchs.
    window.location.assign(next);
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 text-gray-100">
      <form
        onSubmit={submit}
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
            Authentification requise
          </p>
        </div>

        <p className="text-sm text-gray-300">
          Saisis le token configuré dans{" "}
          <code className="rounded bg-white/10 px-1 py-0.5 text-xs">
            DOMESTIQUE_AI_API_TOKEN
          </code>{" "}
          côté serveur.
        </p>

        <label className="block">
          <span className="text-xs text-gray-400">Token</span>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="••••••••••••••••••"
            className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </label>

        {error ? (
          <p className="text-xs text-red-400" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting || !token.trim()}
          className="w-full rounded-lg bg-accent py-2 text-sm font-semibold text-surface transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Connexion…" : "Continuer"}
        </button>
      </form>
    </div>
  );
}
