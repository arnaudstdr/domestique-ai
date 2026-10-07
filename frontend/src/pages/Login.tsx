import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, setApiToken } from "../api/client";
import LegalLinks from "../components/LegalLinks";
import { usePageMeta } from "../hooks/usePageMeta";
import { LEGAL_CONTACT_EMAIL } from "../legal";

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
  const [signupEnabled, setSignupEnabled] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);

  usePageMeta({ title: "Connexion — DomestiqueAI", robots: "noindex" });

  useEffect(() => {
    api.auth
      .config()
      .then((c) => setSignupEnabled(c.signup_enabled))
      .catch(() => setSignupEnabled(false))
      .finally(() => setConfigLoaded(true));
  }, []);

  // Inscriptions fermées : lien mailto pré-rempli vers le contact (ouvre
  // l'application mail par défaut). L'email déjà saisi est glissé dans le corps.
  const inviteMailto = useMemo(() => {
    const subject = "Demande d'inscription à DomestiqueAI";
    const body = [
      "Bonjour,",
      "",
      "Je souhaite obtenir une inscription à DomestiqueAI.",
      "",
      `Adresse email : ${email.trim()}`,
      "Prénom / nom : ",
      "Comment as-tu connu DomestiqueAI ? ",
      "",
      "Merci !",
    ].join("\n");
    return `mailto:${LEGAL_CONTACT_EMAIL}?subject=${encodeURIComponent(
      subject,
    )}&body=${encodeURIComponent(body)}`;
  }, [email]);

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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 text-fg">
      {/* Fond animé : nappes de lumière (transform-only) + profil altimétrique
          au tracé progressif. Purement décoratif. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        <div className="absolute -top-32 left-[6%] h-[26rem] w-[26rem] animate-aurora-a rounded-full bg-accent/[0.16] blur-3xl" />
        <div className="absolute top-1/3 -right-32 h-[24rem] w-[24rem] animate-aurora-b rounded-full bg-ctl/[0.13] blur-3xl" />
        <div className="absolute -bottom-40 left-1/4 h-[26rem] w-[26rem] animate-aurora-c rounded-full bg-tsb/[0.1] blur-3xl" />
        <svg
          viewBox="0 0 1440 320"
          preserveAspectRatio="none"
          fill="none"
          className="absolute inset-x-0 bottom-0 h-40 w-full text-accent/25 sm:h-56"
        >
          <path
            d="M0 268 L96 246 L192 264 L288 206 L384 226 L480 168 L576 198 L672 132 L768 172 L864 108 L960 148 L1056 88 L1152 126 L1248 76 L1344 112 L1440 92"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
            strokeDasharray={1}
            vectorEffect="non-scaling-stroke"
            className="animate-route-draw"
          />
          <path
            d="M0 292 L120 276 L240 288 L360 246 L480 262 L600 218 L720 240 L840 190 L960 214 L1080 164 L1200 188 L1320 142 L1440 162"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
            strokeDasharray={1}
            vectorEffect="non-scaling-stroke"
            className="animate-route-draw opacity-40 [animation-delay:1s]"
          />
        </svg>
      </div>

      <div className="relative w-full max-w-sm">
        <form
          onSubmit={challenge ? submitCode : submitCredentials}
          className="stagger w-full space-y-4 rounded-2xl border border-border/[0.08] bg-linear-to-b from-cardHover/60 to-card/80 p-6 shadow-card backdrop-blur-xl"
        >
          <div className="text-center">
            <div className="relative mx-auto h-16 w-16">
              <div
                aria-hidden="true"
                className="absolute inset-0 animate-coach-halo rounded-2xl bg-accent/30 blur-xl"
              />
              <img
                src="/favicon.svg"
                alt=""
                aria-hidden="true"
                className="relative h-16 w-16 rounded-2xl ring-1 ring-border/10 shadow-card"
              />
            </div>
            <h1 className="mt-3 font-display text-xl font-extrabold tracking-tight">
              Domestique<span className="text-accent">AI</span>
            </h1>
            <p className="mt-1 text-xs text-muted">
              {challenge ? "Vérification en 2 étapes" : "Connexion"}
            </p>
          </div>

          {challenge ? (
            <>
              <p className="text-sm text-fg-soft">
                {useRecovery
                  ? "Saisis l'un de tes codes de secours."
                  : "Saisis le code à 6 chiffres de ton application d'authentification."}
              </p>
              <label className="block">
                <span className="text-xs text-muted">
                  {useRecovery ? "Code de secours" : "Code de vérification"}
                </span>
                <input
                  autoFocus
                  inputMode={useRecovery ? "text" : "numeric"}
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
                  className="input mt-1 tracking-widest"
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  setUseRecovery((v) => !v);
                  setCode("");
                  setError(null);
                }}
                className="text-xs text-muted hover:text-accent"
              >
                {useRecovery
                  ? "Utiliser un code à 6 chiffres"
                  : "Utiliser un code de secours"}
              </button>
            </>
          ) : (
            <>
              <label className="block">
                <span className="text-xs text-muted">Email</span>
                <input
                  type="email"
                  autoFocus
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="toi@exemple.com"
                  className="input mt-1"
                />
              </label>
              <label className="block">
                <span className="text-xs text-muted">Mot de passe</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="••••••••••••"
                  className="input mt-1"
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
            className="btn-primary w-full"
          >
            {submitting
              ? "Connexion…"
              : challenge
                ? "Valider"
                : "Se connecter"}
          </button>

          {!challenge && (
            <div className="flex items-center justify-between text-xs text-muted">
              <Link to="/forgot-password" className="hover:text-accent">
                Mot de passe oublié ?
              </Link>
              {signupEnabled && (
                <Link to="/signup" className="hover:text-accent">
                  Créer un compte
                </Link>
              )}
            </div>
          )}
          <LegalLinks />
        </form>

        {configLoaded && !signupEnabled && !challenge ? (
          <p className="mt-4 animate-rise text-center text-xs text-muted [animation-delay:0.35s]">
            Pas encore de compte ?{" "}
            <a
              href={inviteMailto}
              className="font-semibold text-accent hover:underline"
            >
              Demande une invitation par email
            </a>
          </p>
        ) : null}
      </div>
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
