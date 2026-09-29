import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";

/**
 * Choix d'un nouveau mot de passe (`/reset-password?token=…`). Le reset révoque
 * toutes les sessions existantes côté serveur.
 */
export default function ResetPassword() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "Lien invalide (token manquant).");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.auth.resetPassword(token, password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setError("Mot de passe trop court (10 caractères minimum).");
      } else if (err instanceof ApiError && err.status === 400) {
        setError("Lien de réinitialisation invalide ou expiré.");
      } else {
        setError("Échec de la réinitialisation.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 py-8 text-fg">
      <div className="card w-full max-w-md space-y-4 p-6">
        <div className="text-center">
          <h1 className="font-display text-xl font-extrabold tracking-tight">
            Nouveau mot de passe
          </h1>
        </div>
        {done ? (
          <>
            <p className="text-sm text-fg-soft">
              Ton mot de passe a été modifié. Toutes tes sessions ont été déconnectées.
            </p>
            <Link to="/login" className="btn-primary inline-block w-full text-center">
              Se connecter
            </Link>
          </>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <span className="text-xs text-muted">Nouveau mot de passe</span>
              <input
                type="password"
                autoFocus
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="10 caractères minimum"
                className="input mt-1 w-full"
              />
            </label>
            <label className="block">
              <span className="text-xs text-muted">Confirmer le mot de passe</span>
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
              disabled={submitting || !token || password.length < 10 || password !== confirm}
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Enregistrement…" : "Changer mon mot de passe"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
