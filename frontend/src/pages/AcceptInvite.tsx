import { useState } from "react";
import { api, ApiError, setApiToken } from "../api/client";

/**
 * Acceptation d'une invitation (multi-tenant).
 *
 * Ouvert via le lien `/accept-invite?token=<invite_token>`. On échange l'invite
 * contre un token de session (POST /api/auth/accept-invite), qu'on stocke comme
 * Bearer — l'app fonctionne ensuite normalement. Le propriétaire, lui, continue
 * de passer par /login (token API collé).
 */
export default function AcceptInvite() {
  const params = new URLSearchParams(window.location.search);
  const inviteToken = params.get("token") || "";
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(
    inviteToken ? null : "Lien d'invitation invalide (token manquant).",
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!inviteToken) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.acceptInvite(inviteToken, displayName.trim());
      setApiToken(res.session_token);
      window.location.assign("/");
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 400
          ? "Invitation invalide, expirée ou déjà utilisée."
          : "Échec de l'acceptation de l'invitation.";
      setError(msg);
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 text-gray-100">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
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
          <p className="mt-1 text-xs text-gray-400">Rejoindre via une invitation</p>
        </div>

        <p className="text-sm text-gray-300">
          Choisis ton nom d'affichage, puis crée ton compte. Ton coach
          synchronisera tes activités depuis son espace.
        </p>

        <label className="block">
          <span className="text-xs text-gray-400">Nom d'affichage (optionnel)</span>
          <input
            type="text"
            autoFocus
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="Alice"
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
          disabled={submitting || !inviteToken}
          className="w-full rounded-lg bg-accent py-2 text-sm font-semibold text-surface transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Création…" : "Créer mon compte"}
        </button>

        <p className="text-center text-xs text-gray-500">
          <a href="/login" className="hover:text-accent">
            J'ai déjà un token d'accès
          </a>
        </p>
      </form>
    </div>
  );
}
