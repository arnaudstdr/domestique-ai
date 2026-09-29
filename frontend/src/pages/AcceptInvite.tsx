import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, setApiToken } from "../api/client";
import TwoFactorSetup from "../components/TwoFactorSetup";

/**
 * Acceptation d'une invitation (multi-tenant).
 *
 * Ouvert via `/accept-invite?token=<token>` (invitation à usage unique) ou
 * `/accept-invite?coach=<code>` (lien réutilisable d'un coach).
 *
 * Deux parcours :
 * 1. « Créer un compte » — nouveau compte athlète, puis enrôlement TOTP ;
 * 2. « J'ai déjà un compte » — connexion (mdp + TOTP) du compte existant, puis
 *    rattachement au coach (aucun doublon de compte).
 */
export default function AcceptInvite() {
  const params = new URLSearchParams(window.location.search);
  const inviteToken = params.get("token") || "";
  const coachCode = params.get("coach") || "";
  const hasTarget = Boolean(inviteToken || coachCode);

  const [mode, setMode] = useState<"create" | "existing">("create");
  const [stage, setStage] = useState<"form" | "totp">("form");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(
    hasTarget ? null : "Lien d'invitation invalide (paramètre manquant).",
  );

  async function createAccount(event: React.FormEvent) {
    event.preventDefault();
    if (!hasTarget) return;
    if (password !== confirm) {
      setError("Les deux mots de passe ne correspondent pas.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.acceptInvite({
        inviteToken: inviteToken || null,
        coachCode: coachCode || null,
        displayName: displayName.trim() || null,
        email: email.trim(),
        password,
      });
      setApiToken(res.session_token);
      setStage("totp");
    } catch (err) {
      setError(acceptErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function linkExisting(sessionToken: string) {
    setApiToken(sessionToken);
    await api.auth.acceptInviteLink({
      inviteToken: inviteToken || null,
      coachCode: coachCode || null,
    });
    window.location.assign("/");
  }

  async function linkSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!hasTarget) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.login(email.trim(), password);
      if (res.status === "totp_required" && res.challenge) {
        setChallenge(res.challenge);
        return;
      }
      if (res.session_token) {
        await linkExisting(res.session_token);
        return;
      }
      setError("Réponse inattendue du serveur.");
    } catch (err) {
      setError(acceptErrorMessage(err, "Connexion impossible : email ou mot de passe incorrect."));
    } finally {
      setSubmitting(false);
    }
  }

  async function linkTotp(event: React.FormEvent) {
    event.preventDefault();
    if (!challenge || !code.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.loginTotp(challenge, code.trim());
      if (res.session_token) {
        await linkExisting(res.session_token);
        return;
      }
      setError("Réponse inattendue du serveur.");
    } catch (err) {
      setError(acceptErrorMessage(err, "Code de vérification incorrect."));
    } finally {
      setSubmitting(false);
    }
  }

  function submit(event: React.FormEvent) {
    if (challenge) return linkTotp(event);
    return mode === "existing" ? linkSubmit(event) : createAccount(event);
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
          <p className="mt-1 text-xs text-muted">Rejoindre un coach</p>
        </div>

        {stage === "totp" ? (
          <TwoFactorSetup onDone={() => window.location.assign("/")} />
        ) : (
          <form onSubmit={submit} className="space-y-4">
            {!challenge && (
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    { value: "create", label: "Créer un compte" },
                    { value: "existing", label: "J'ai déjà un compte" },
                  ] as const
                ).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => {
                      setMode(option.value);
                      setError(null);
                    }}
                    className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                      mode === option.value
                        ? "border-accent bg-accent/15 text-accent"
                        : "border-border/10 text-fg-soft hover:border-accent/40"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            )}

            {challenge ? (
              <label className="block">
                <span className="text-xs text-muted">Code de vérification (2FA)</span>
                <input
                  autoFocus
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder="123456 ou code de secours"
                  className="input mt-1 w-full tracking-widest"
                />
              </label>
            ) : (
              <>
                {mode === "create" && (
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
                )}
                <label className="block">
                  <span className="text-xs text-muted">Email</span>
                  <input
                    type="email"
                    autoFocus={mode === "existing"}
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
                    autoComplete={mode === "create" ? "new-password" : "current-password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder={mode === "create" ? "10 caractères minimum" : "••••••••••••"}
                    className="input mt-1 w-full"
                  />
                </label>
                {mode === "create" && (
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
                )}
              </>
            )}

            {error ? (
              <p className="text-xs text-red-400" role="alert">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={
                submitting ||
                !hasTarget ||
                (challenge
                  ? !code.trim()
                  : !email.trim() ||
                    password.length < 10 ||
                    (mode === "create" && password !== confirm))
              }
              className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {challenge
                ? "Valider"
                : mode === "existing"
                  ? submitting
                    ? "Connexion…"
                    : "Se connecter et rejoindre"
                  : submitting
                    ? "Création…"
                    : "Créer mon compte"}
            </button>

            <p className="text-center text-xs text-muted">
              <Link to="/login" className="hover:text-accent">
                J'ai déjà un compte
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}

function acceptErrorMessage(err: unknown, fallback = "Échec de l'acceptation de l'invitation."): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return "Cet email est déjà utilisé. Connecte-toi pour rejoindre ce coach.";
    if (err.status === 422) return "Mot de passe trop court (10 caractères minimum).";
    if (err.status === 400) return "Invitation invalide, expirée ou déjà utilisée.";
    if (err.status === 401) return fallback;
    if (err.status === 429) return "Trop de tentatives. Réessaie dans un moment.";
    if (err.status === 403) return "Ton compte doit d'abord terminer sa configuration 2FA.";
  }
  return fallback;
}
