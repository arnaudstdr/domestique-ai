import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";

/**
 * Demande de réinitialisation de mot de passe. Réponse toujours positive côté
 * serveur (anti-énumération) : on affiche le même message que l'email existe.
 */
export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("Trop de demandes. Réessaie dans un moment.");
      } else {
        setError("Échec de la demande.");
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
            Mot de passe oublié
          </h1>
        </div>
        {sent ? (
          <p className="text-sm text-fg-soft">
            Si un compte existe pour cette adresse, un lien de réinitialisation vient d'être
            envoyé. Pense à vérifier tes spams.
          </p>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <span className="text-xs text-muted">Email</span>
              <input
                type="email"
                autoFocus
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="toi@exemple.com"
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
              disabled={submitting || !email.trim()}
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Envoi…" : "Envoyer le lien"}
            </button>
          </form>
        )}
        <p className="text-center text-xs text-muted">
          <Link to="/login" className="hover:text-accent">
            Retour à la connexion
          </Link>
        </p>
      </div>
    </div>
  );
}
