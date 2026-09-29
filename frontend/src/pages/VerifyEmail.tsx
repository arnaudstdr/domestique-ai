import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

/**
 * Vérification d'email (`/verify-email?token=…`) : consomme le token reçu par
 * mail. Non bloquant — l'app reste accessible si l'email n'est pas confirmé.
 */
export default function VerifyEmail() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [state, setState] = useState<"pending" | "ok" | "error">("pending");

  useEffect(() => {
    if (!token) {
      setState("error");
      return;
    }
    api.auth
      .verifyEmail(token)
      .then(() => setState("ok"))
      .catch(() => setState("error"));
  }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 py-8 text-fg">
      <div className="card w-full max-w-md space-y-4 p-6 text-center">
        <img
          src="/icon-192.png"
          alt=""
          aria-hidden="true"
          className="mx-auto h-16 w-16 rounded-2xl ring-1 ring-border/10 shadow-card"
        />
        <h1 className="font-display text-xl font-extrabold tracking-tight">
          Domestique<span className="text-accent">AI</span>
        </h1>
        {state === "pending" ? (
          <p className="text-sm text-fg-soft">Vérification en cours…</p>
        ) : state === "ok" ? (
          <>
            <p className="text-sm text-fg-soft">Ton adresse email est confirmée. Merci !</p>
            <Link to="/" className="btn-primary inline-block w-full">
              Continuer
            </Link>
          </>
        ) : (
          <>
            <p className="text-sm text-red-400">
              Lien de vérification invalide ou expiré.
            </p>
            <Link to="/" className="btn-ghost inline-block w-full">
              Retour
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
