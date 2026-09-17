import { useState } from "react";
import { api, ApiError, setApiToken } from "../api/client";
import TwoFactorSetup from "../components/TwoFactorSetup";

/**
 * Acceptation d'une invitation (multi-tenant) — assistant en 2 temps :
 *
 * 1. email + mot de passe → crée le compte et ouvre une session ;
 * 2. enrôlement TOTP obligatoire (QR + codes de secours).
 *
 * Ouvert via `/accept-invite?token=<invite_token>`.
 */
export default function AcceptInvite() {
  const inviteToken = new URLSearchParams(window.location.search).get("token") || "";
  const [stage, setStage] = useState<"credentials" | "totp">("credentials");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(
    inviteToken ? null : "Lien d'invitation invalide (token manquant).",
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!inviteToken) return;
    if (password !== confirm) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.acceptInvite(
        inviteToken,
        displayName.trim() || null,
        email.trim(),
        password,
      );
      setApiToken(res.session_token);
      setStage("totp");
    } catch (err) {
      setError(acceptErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 py-8 text-gray-100">
      <div className="card w-full max-w-md space-y-4 p-6">
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

        {stage === "totp" ? (
          <TwoFactorSetup onDone={() => window.location.assign("/")} />
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <p className="text-sm text-gray-300">
              Crée ton compte : choisis un email et un mot de passe, puis active
              la double authentification.
            </p>
            <label className="block">
              <span className="text-xs text-gray-400">Nom d'affichage (optionnel)</span>
              <input
                type="text"
                autoFocus
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Alice"
                className="input mt-1 w-full"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Email</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="toi@exemple.com"
                className="input mt-1 w-full"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Mot de passe</span>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="10 caractères minimum"
                className="input mt-1 w-full"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">Confirmer le mot de passe</span>
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
              disabled={
                submitting ||
                !inviteToken ||
                !email.trim() ||
                password.length < 10 ||
                password !== confirm
              }
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Création…" : "Créer mon compte"}
            </button>

            <p className="text-center text-xs text-gray-500">
              <a href="/login" className="hover:text-accent">
                J'ai déjà un compte
              </a>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}

function acceptErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return "Cet email est déjà utilisé.";
    if (err.status === 422) return "Mot de passe trop court (10 caractères minimum).";
    if (err.status === 400) return "Invitation invalide, expirée ou déjà utilisée.";
  }
  return "Échec de l'acceptation de l'invitation.";
}
