import { useState } from "react";
import { ApiError, api } from "../api/client";
import { useMe, useMeRefresh } from "../hooks/useMe";
import ConsentCheckboxes from "./ConsentCheckboxes";

/**
 * Portail de consentement bloquant pour les comptes sans consentement
 * enregistré (comptes créés avant la mise en conformité, ou dont la version
 * acceptée est absente). Un compte qui a retiré son consentement santé n'est
 * PAS bloqué ici : le retrait s'exerce depuis le profil.
 */
export default function ConsentGate() {
  const me = useMe();
  const refresh = useMeRefresh();
  const [acceptsTerms, setAcceptsTerms] = useState(false);
  const [acceptsHealthData, setAcceptsHealthData] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!me || (me.terms_accepted_at && me.health_consent_at)) return null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.auth.acceptConsents(acceptsTerms, acceptsHealthData);
      refresh();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Échec de l'enregistrement des consentements.",
      );
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[2000] grid place-items-center overflow-y-auto bg-surface/95 px-4 py-8 backdrop-blur-sm">
      <form onSubmit={submit} className="card w-full max-w-md space-y-4 p-6">
        <div className="text-center">
          <h1 className="font-display text-lg font-extrabold tracking-tight">
            Avant de continuer
          </h1>
          <p className="mt-1 text-xs text-muted">
            DomestiqueAI traite des données de santé : ton consentement est requis.
          </p>
        </div>

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
          disabled={submitting || !acceptsTerms || !acceptsHealthData}
          className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Enregistrement…" : "Accepter et continuer"}
        </button>
      </form>
    </div>
  );
}
