import { useEffect, useState } from "react";
import { Check, Copy, Download, ShieldCheck } from "lucide-react";
import { ApiError, api } from "../api/client";
import type { TotpEnrollResponse } from "../api/types";

/**
 * Assistant d'enrôlement TOTP : scan du QR → confirmation d'un code →
 * affichage une seule fois des codes de secours. Appelle `onDone` à la fin.
 */
export default function TwoFactorSetup({ onDone }: { onDone: () => void }) {
  const [enroll, setEnroll] = useState<TotpEnrollResponse | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    api.auth
      .totpEnroll()
      .then((res) => alive && setEnroll(res))
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof ApiError ? err.message : "Échec de l'enrôlement.");
      });
    return () => {
      alive = false;
    };
  }, []);

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    if (!code.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.auth.totpVerify(code.trim());
      setRecoveryCodes(res.recovery_codes);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 400
          ? "Code invalide. Réessaie avec le code courant."
          : "Échec de la vérification.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function copyCodes() {
    if (!recoveryCodes) return;
    const text = recoveryCodes.join("\n");
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        return;
      }
    } catch {
      // clipboard indisponible → fallback download
    }
    downloadCodes();
  }

  function downloadCodes() {
    if (!recoveryCodes) return;
    const blob = new Blob([recoveryCodes.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "domestique-ai-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (recoveryCodes) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-accent">
          <ShieldCheck className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
          <h3 className="font-display text-lg font-extrabold">Codes de secours</h3>
        </div>
        <p className="text-sm text-gray-300">
          Conserve ces codes en lieu sûr : ils permettent de te connecter si tu
          perds l'accès à ton application d'authentification. Chaque code n'est
          utilisable qu'une fois — ils ne seront plus affichés.
        </p>
        <ul className="grid grid-cols-2 gap-2 rounded-xl border border-accent/30 bg-accent/[0.06] p-3 font-mono text-sm">
          {recoveryCodes.map((c) => (
            <li key={c} className="tracking-wider text-gray-100">
              {c}
            </li>
          ))}
        </ul>
        <div className="flex gap-2">
          <button type="button" onClick={copyCodes} className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs">
            {copied ? (
              <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            ) : (
              <Copy className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            {copied ? "Copié" : "Copier"}
          </button>
          <button
            type="button"
            onClick={downloadCodes}
            className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs"
          >
            <Download className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Télécharger
          </button>
        </div>
        <label className="flex items-start gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            className="mt-0.5"
          />
          J'ai noté mes codes de secours.
        </label>
        <button
          type="button"
          disabled={!acknowledged}
          onClick={onDone}
          className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
        >
          Terminer
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={verify} className="space-y-4">
      <div className="flex items-center gap-2 text-accent">
        <ShieldCheck className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
        <h3 className="font-display text-lg font-extrabold">Activer la 2FA</h3>
      </div>
      <p className="text-sm text-gray-300">
        Scanne ce QR code avec ton application d'authentification (Google
        Authenticator, Authy, 1Password…), puis saisis le code à 6 chiffres.
      </p>
      {enroll ? (
        <>
          <img
            src={enroll.qr_svg_data_uri}
            alt="QR code d'enrôlement TOTP"
            className="mx-auto h-48 w-48 rounded-xl bg-white p-2 ring-1 ring-white/10"
          />
          <p className="text-center text-xs text-muted">
            Ou saisis la clé manuellement :{" "}
            <code className="select-all rounded bg-white/10 px-1 py-0.5 font-mono text-[11px]">
              {enroll.secret}
            </code>
          </p>
        </>
      ) : (
        <p className="text-center text-xs text-muted">Génération du QR code…</p>
      )}
      <label className="block">
        <span className="text-xs text-gray-400">Code de vérification</span>
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          placeholder="123456"
          className="input mt-1 w-full tracking-widest"
        />
      </label>
      {error ? (
        <p className="text-xs text-red-400" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={submitting || !enroll || !code.trim()}
        className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? "Vérification…" : "Activer"}
      </button>
    </form>
  );
}
