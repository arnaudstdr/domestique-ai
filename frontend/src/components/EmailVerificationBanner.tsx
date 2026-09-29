import { useState } from "react";
import { MailWarning, X } from "lucide-react";
import { api } from "../api/client";
import { useMe } from "../hooks/useMe";

/**
 * Bandeau de rappel de vérification d'email (non bloquant). Affiché tant que le
 * compte n'a pas confirmé son adresse ; renvoi de l'email à la demande.
 */
export default function EmailVerificationBanner() {
  const me = useMe();
  const [dismissed, setDismissed] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  if (!me || me.email_verified || dismissed) return null;

  async function resend() {
    setSending(true);
    try {
      await api.auth.resendVerification();
      setSent(true);
    } catch {
      // best-effort : on reste silencieux
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="bg-accent/10 border-b border-accent/20">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-4 py-2">
        <span className="flex min-w-0 items-center gap-2 text-xs text-accent">
          <MailWarning className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span className="truncate">
            {sent ? "Email de vérification envoyé." : "Confirme ton adresse email."}
          </span>
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {!sent && (
            <button
              type="button"
              onClick={resend}
              disabled={sending}
              className="btn-ghost px-3 py-1.5 text-xs disabled:opacity-50"
            >
              {sending ? "Envoi…" : "Renvoyer"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setDismissed(true)}
            aria-label="Masquer"
            className="btn-ghost p-1.5 text-xs"
          >
            <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
