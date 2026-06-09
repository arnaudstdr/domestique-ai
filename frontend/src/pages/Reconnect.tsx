import { useEffect, useState } from "react";
import { api, ApiError, setApiToken } from "../api/client";

/**
 * Reconnexion à un compte athlète existant via un lien fourni par le coach.
 *
 * Ouvert via `/reconnect?token=<reconnect_token>`. On échange le token (usage
 * unique) contre une nouvelle session (POST /api/auth/reconnect), qu'on stocke
 * comme Bearer, puis on redirige vers le dashboard. Sert quand l'athlète s'est
 * déconnecté (session révoquée, invitation déjà consommée).
 */
export default function Reconnect() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [error, setError] = useState<string | null>(
    token ? null : "Lien de reconnexion invalide (token manquant).",
  );

  useEffect(() => {
    if (!token) return;
    let alive = true;
    api.auth
      .reconnect(token)
      .then((res) => {
        setApiToken(res.session_token);
        window.location.assign("/");
      })
      .catch((err) => {
        if (!alive) return;
        const msg =
          err instanceof ApiError && err.status === 400
            ? "Lien de reconnexion invalide, expiré ou déjà utilisé."
            : "Échec de la reconnexion.";
        setError(msg);
      });
    return () => {
      alive = false;
    };
  }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4 text-gray-100">
      <div className="card w-full max-w-sm space-y-4 p-6 text-center">
        <img
          src="/icon-192.png"
          alt=""
          aria-hidden="true"
          className="mx-auto h-16 w-16 rounded-2xl ring-1 ring-white/10 shadow-card"
        />
        <h1 className="font-display text-xl font-extrabold tracking-tight">
          Domestique<span className="text-accent">AI</span>
        </h1>
        {error ? (
          <>
            <p className="text-sm text-red-400" role="alert">
              {error}
            </p>
            <p className="text-xs text-gray-500">
              Demande un nouveau lien à ton coach, ou{" "}
              <a href="/login" className="hover:text-accent">
                connecte-toi avec un token
              </a>
              .
            </p>
          </>
        ) : (
          <p className="text-sm text-gray-300">Reconnexion en cours…</p>
        )}
      </div>
    </div>
  );
}
