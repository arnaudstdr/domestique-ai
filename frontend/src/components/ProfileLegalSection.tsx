import { useState } from "react";
import { Link } from "react-router-dom";
import { Download, FileText, HeartPulse } from "lucide-react";
import { ApiError, api } from "../api/client";
import { useMe, useMeRefresh } from "../hooks/useMe";
import { useToast } from "../hooks/useToast";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

/**
 * Section « Données personnelles & consentements » du profil : état des
 * consentements, retrait/réactivation du consentement santé (art. 7.3 RGPD),
 * export des données (portabilité) et liens vers les documents légaux.
 */
export default function ProfileLegalSection() {
  const me = useMe();
  const refreshMe = useMeRefresh();
  const { push } = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);

  if (!me) return null;

  const healthActive = Boolean(me.health_consent_at) && !me.health_consent_withdrawn_at;

  async function exportData() {
    setBusy("export");
    try {
      const { blob, filename } = await api.auth.exportData();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      push("Export téléchargé.", "success");
    } catch (err) {
      push(err instanceof ApiError ? err.message : "Export impossible.", "error");
    } finally {
      setBusy(null);
    }
  }

  async function reacceptHealth() {
    setBusy("health");
    try {
      await api.auth.acceptConsents(true, true);
      refreshMe();
      push("Consentement santé enregistré.", "success");
    } catch (err) {
      push(err instanceof ApiError ? err.message : "Enregistrement impossible.", "error");
    } finally {
      setBusy(null);
    }
  }

  async function withdrawHealth() {
    setBusy("health");
    try {
      await api.auth.withdrawHealthConsent();
      refreshMe();
      setConfirmWithdraw(false);
      push("Consentement retiré, sources de santé déconnectées.", "success");
    } catch (err) {
      push(err instanceof ApiError ? err.message : "Retrait impossible.", "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-fg-soft">
        <FileText className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Données personnelles & consentements
      </h3>

      <div className="space-y-1 text-xs text-muted">
        <p>
          CGU et politique de confidentialité :{" "}
          {me.terms_accepted_at ? (
            <span className="text-fg-soft">
              acceptées le {formatDate(me.terms_accepted_at)} (v{me.terms_accepted_version})
            </span>
          ) : (
            <span className="text-amber-400">non acceptées</span>
          )}
        </p>
        <p className="flex items-center gap-1.5">
          <HeartPulse className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Données de santé :{" "}
          {healthActive ? (
            <span className="text-fg-soft">
              consentement actif depuis le {formatDate(me.health_consent_at)} (v
              {me.health_consent_version})
            </span>
          ) : me.health_consent_withdrawn_at ? (
            <span className="text-amber-400">
              consentement retiré le {formatDate(me.health_consent_withdrawn_at)}
            </span>
          ) : (
            <span className="text-amber-400">non accepté</span>
          )}
        </p>
        <p>
          Documents :{" "}
          <Link to="/cgu" className="text-accent hover:underline">
            CGU
          </Link>
          {" · "}
          <Link to="/confidentialite" className="text-accent hover:underline">
            Confidentialité
          </Link>
          {" · "}
          <Link to="/mentions-legales" className="text-accent hover:underline">
            Mentions légales
          </Link>
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={exportData}
          disabled={busy !== null}
          className="btn-ghost flex w-full items-center justify-center gap-2 disabled:opacity-50"
        >
          <Download className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          {busy === "export" ? "Préparation de l'export…" : "Télécharger mes données"}
        </button>

        {healthActive ? (
          confirmWithdraw ? (
            <div className="space-y-2 rounded-lg border border-amber-500/30 p-3">
              <p className="text-xs text-muted">
                Le retrait déconnecte Garmin et Google Health. Les données déjà
                collectées restent consultables ; tu peux les effacer via la
                suppression de compte (zone de danger).
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={withdrawHealth}
                  disabled={busy !== null}
                  className="btn-ghost flex-1 border-amber-500/40 text-amber-400 disabled:opacity-50"
                >
                  {busy === "health" ? "Retrait…" : "Confirmer le retrait"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmWithdraw(false)}
                  className="btn-ghost flex-1"
                >
                  Annuler
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmWithdraw(true)}
              className="btn-ghost w-full text-muted"
            >
              Retirer mon consentement santé
            </button>
          )
        ) : (
          <button
            type="button"
            onClick={reacceptHealth}
            disabled={busy !== null}
            className="btn-ghost w-full disabled:opacity-50"
          >
            {busy === "health"
              ? "Enregistrement…"
              : "Donner mon consentement santé (reconnecter Garmin / Google Health ensuite)"}
          </button>
        )}
      </div>
    </section>
  );
}
