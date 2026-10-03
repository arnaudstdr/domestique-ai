import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, setApiToken } from "../api/client";
import ConsentCheckboxes from "../components/ConsentCheckboxes";
import LegalLinks from "../components/LegalLinks";
import TwoFactorSetup from "../components/TwoFactorSetup";
import { usePageMeta } from "../hooks/usePageMeta";

/**
 * Inscription self-service (si activée côté serveur) : email + mot de passe +
 * choix du rôle (athlète ou coach), puis enrôlement TOTP obligatoire.
 *
 * Un coach reçoit à l'inscription son lien d'invitation réutilisable (visible
 * ensuite dans Roster).
 */
export default function Signup() {
  const [signupEnabled, setSignupEnabled] = useState<boolean | null>(null);
  const [role, setRole] = useState<"athlete" | "coach">("athlete");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [stage, setStage] = useState<"form" | "totp">("form");
  const [isCoach, setIsCoach] = useState(false);
  const [acceptsTerms, setAcceptsTerms] = useState(false);
  const [acceptsHealthData, setAcceptsHealthData] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePageMeta({ title: "Créer un compte — DomestiqueAI", robots: "noindex" });

  useEffect(() => {
    api.auth
      .config()
      .then((c) => setSignupEnabled(c.signup_enabled))
      .catch(() => setSignupEnabled(false));
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    if (!acceptsTerms || !acceptsHealthData) {
      setError("Les deux consentements sont requis pour créer un compte.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.signup(
        email.trim(),
        password,
        role,
        displayName.trim() || null,
        acceptsTerms,
        acceptsHealthData,
      );
      setApiToken(res.session_token);
      setIsCoach(res.role === "coach");
      setStage("totp");
    } catch (err) {
      setError(signupErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  function done() {
    // Le coach atterrit sur Roster (son lien d'invitation y est affiché).
    window.location.assign(isCoach ? "/roster" : "/");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 py-8 text-fg">
      <div className="card w-full max-w-md space-y-4 p-6">
        <div className="text-center">
          <img
            src="/icon-192.png"
            alt=""
            aria-hidden="true"
            className="mx-auto h-16 w-16 rounded-2xl ring-1 ring-border/10 shadow-card"
          />
          <h1 className="mt-3 font-display text-xl font-extrabold tracking-tight">
            Domestique<span className="text-accent">AI</span>
          </h1>
          <p className="mt-1 text-xs text-muted">Créer un compte</p>
        </div>

        {signupEnabled === false ? (
          <p className="text-sm text-fg-soft text-center">
            L'inscription publique est désactivée. Demande une invitation à ton coach.
          </p>
        ) : stage === "totp" ? (
          <TwoFactorSetup onDone={done} />
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  { value: "athlete", label: "Athlète" },
                  { value: "coach", label: "Coach" },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setRole(option.value)}
                  className={`rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                    role === option.value
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-border/10 text-fg-soft hover:border-accent/40"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <label className="block">
              <span className="text-xs text-muted">Nom d'affichage (optionnel)</span>
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
              <span className="text-xs text-muted">Email</span>
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
              <span className="text-xs text-muted">Mot de passe</span>
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
              <span className="text-xs text-muted">Confirmer le mot de passe</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="input mt-1 w-full"
              />
            </label>

            <ConsentCheckboxes
              acceptsTerms={acceptsTerms}
              acceptsHealthData={acceptsHealthData}
              onChangeTerms={setAcceptsTerms}
              onChangeHealthData={setAcceptsHealthData}
              disabled={submitting}
            />

            {error ? (
              <p className="text-xs text-red-400" role="alert">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={
                submitting ||
                !email.trim() ||
                password.length < 10 ||
                password !== confirm ||
                !acceptsTerms ||
                !acceptsHealthData ||
                signupEnabled === null
              }
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Création…" : "Créer mon compte"}
            </button>

            <p className="text-center text-xs text-muted">
              <Link to="/login" className="hover:text-accent">
                J'ai déjà un compte
              </Link>
            </p>
            <LegalLinks />
          </form>
        )}
      </div>
    </div>
  );
}

function signupErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "L'inscription publique est désactivée.";
    if (err.status === 409) return "Cet email est déjà utilisé.";
    if (err.status === 422) return err.message || "Mot de passe trop court (10 caractères minimum).";
    if (err.status === 429) return "Trop de tentatives. Réessaie dans un moment.";
  }
  return "Échec de l'inscription.";
}
