import { useEffect, useState } from "react";
import { Loader2, RotateCcw, ShieldCheck } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { AdminFeedback, AdminSettings, AdminUser } from "../api/types";
import { useToast } from "../hooks/useToast";

const ROLES = ["coach", "athlete", "admin"];

function errMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

export default function Admin() {
  const { push } = useToast();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [feedback, setFeedback] = useState<AdminFeedback[] | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);

  useEffect(() => {
    api.admin.users().then(setUsers).catch((e) => push(`Comptes : ${errMessage(e)}`, "error"));
    api.admin
      .settings()
      .then(setSettings)
      .catch((e) => push(`Réglages : ${errMessage(e)}`, "error"));
    api.admin
      .feedback()
      .then(setFeedback)
      .catch((e) => push(`Retours : ${errMessage(e)}`, "error"));
  }, [push]);

  async function changeRole(user: AdminUser, role: string) {
    setSavingId(user.public_id);
    try {
      const updated = await api.admin.setRole(user.public_id, role);
      setUsers((prev) =>
        prev ? prev.map((u) => (u.public_id === updated.public_id ? updated : u)) : prev,
      );
      push(`Rôle de ${updated.public_id.slice(0, 8)} : ${updated.role}`, "success");
    } catch (err) {
      push(`Changement de rôle : ${errMessage(err)}`, "error");
    } finally {
      setSavingId(null);
    }
  }

  async function reset2fa(user: AdminUser) {
    const label = user.email || user.public_id;
    if (
      !window.confirm(
        `Réinitialiser la 2FA de ${label} ?\n\nLe compte devra ré-enrôler un code à la prochaine connexion.`,
      )
    ) {
      return;
    }
    setSavingId(user.public_id);
    try {
      const updated = await api.admin.reset2fa(user.public_id);
      setUsers((prev) =>
        prev ? prev.map((u) => (u.public_id === updated.public_id ? updated : u)) : prev,
      );
      push(`2FA réinitialisée pour ${updated.public_id.slice(0, 8)}`, "success");
    } catch (err) {
      push(`Réinitialisation 2FA : ${errMessage(err)}`, "error");
    } finally {
      setSavingId(null);
    }
  }

  async function toggleSignup(next: boolean) {
    setSavingSettings(true);
    try {
      const updated = await api.admin.updateSettings({ signup_enabled: next });
      setSettings(updated);
      push(`Inscription publique : ${updated.signup_enabled ? "activée" : "désactivée"}`, "success");
    } catch (err) {
      push(`Réglages : ${errMessage(err)}`, "error");
    } finally {
      setSavingSettings(false);
    }
  }

  return (
    <div className="stagger space-y-6">
      <div>
        <h2 className="flex items-center gap-2 font-display text-2xl font-extrabold tracking-tight text-fg">
          <ShieldCheck className="h-6 w-6 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Administration
        </h2>
        <p className="mt-1 text-sm text-muted">
          Comptes, retours testeurs et réglages de la plateforme.
        </p>
      </div>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Réglages plateforme</h3>
        {settings === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : (
          <label className="flex items-center justify-between gap-4">
            <span className="text-sm text-fg-soft">
              Inscription publique
              <span className="block text-xs text-muted">
                Autorise la création de compte via /signup (override en base).
              </span>
            </span>
            <input
              type="checkbox"
              checked={settings.signup_enabled}
              disabled={savingSettings}
              onChange={(e) => toggleSignup(e.target.checked)}
              className="h-5 w-5 shrink-0"
            />
          </label>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Comptes</h3>
        {users === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : (
          <ul className="divide-y divide-border/[0.06]">
            {users.map((u) => (
              <li key={u.public_id} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-fg">{u.email || u.public_id}</div>
                  <div className="truncate text-xs text-muted">
                    {u.display_name || "—"}
                    {u.is_bootstrap ? " · propriétaire" : ""}
                    {u.totp_enabled ? " · 2FA" : " · sans-2FA"}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {u.totp_enabled && (
                    <button
                      type="button"
                      onClick={() => reset2fa(u)}
                      disabled={savingId === u.public_id}
                      aria-label="Réinitialiser la 2FA"
                      title="Réinitialiser la 2FA"
                      className="grid h-9 w-9 place-items-center rounded-xl text-fg-soft
                                 border border-border/[0.06] bg-overlay/[0.03]
                                 hover:text-accent hover:border-accent/40 transition-colors
                                 disabled:opacity-50"
                    >
                      <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  )}
                  {u.is_bootstrap ? (
                    <span className="text-xs text-muted">{u.role}</span>
                  ) : (
                    <select
                      value={u.role}
                      disabled={savingId === u.public_id}
                      onChange={(e) => changeRole(u, e.target.value)}
                      className="input w-auto"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Retours testeurs</h3>
        {feedback === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : feedback.length === 0 ? (
          <p className="text-sm text-muted">Aucun retour pour l'instant.</p>
        ) : (
          <ul className="space-y-3">
            {feedback.map((f) => (
              <li key={f.id} className="rounded-xl border border-border/[0.06] p-3">
                <div className="flex items-center justify-between gap-2 text-xs text-muted">
                  <span className="font-semibold text-accent">{f.category}</span>
                  <span>{new Date(f.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-fg-soft">{f.message}</p>
                <div className="mt-1 text-xs text-muted">
                  {f.author_email || f.public_id || "anonyme"}
                  {f.page ? ` · ${f.page}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
