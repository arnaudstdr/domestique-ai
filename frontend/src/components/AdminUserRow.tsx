import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  KeyRound,
  Loader2,
  LogOut,
  MailCheck,
  RotateCcw,
  Trash2,
  Unlock,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { AdminSession, AdminUser, AdminUserDetail } from "../api/types";
import { useToast } from "../hooks/useToast";

const ROLES = ["coach", "athlete", "admin"];

function errMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-md border px-1.5 py-0.5 text-[11px] ${
        ok
          ? "border-green-500/30 bg-green-500/10 text-green-400"
          : "border-border/[0.08] bg-overlay/[0.03] text-muted"
      }`}
    >
      {label}
    </span>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  busy,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  busy: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors disabled:opacity-50 ${
        danger
          ? "border-red-500/30 bg-red-500/[0.06] text-red-400 hover:border-red-500/50"
          : "border-border/[0.06] bg-overlay/[0.03] text-fg-soft hover:text-accent hover:border-accent/40"
      }`}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      {label}
    </button>
  );
}

export default function AdminUserRow({
  user,
  onUpdated,
  onDeleted,
}: {
  user: AdminUser;
  onUpdated: (u: AdminUser) => void;
  onDeleted: (publicId: string) => void;
}) {
  const { push } = useToast();
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);
  const [sessions, setSessions] = useState<AdminSession[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const label = user.email || user.public_id;

  async function loadDetail() {
    try {
      const [d, s] = await Promise.all([
        api.admin.userDetail(user.public_id),
        api.admin.sessions(user.public_id),
      ]);
      setDetail(d);
      setSessions(s);
    } catch (err) {
      push(`Fiche compte : ${errMessage(err)}`, "error");
    }
  }

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null) void loadDetail();
  }

  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind);
    try {
      await fn();
    } catch (err) {
      push(`${kind} : ${errMessage(err)}`, "error");
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(role: string) {
    await run("Rôle", async () => {
      const updated = await api.admin.setRole(user.public_id, role);
      onUpdated(updated);
      push(`Rôle de ${user.public_id.slice(0, 8)} : ${updated.role}`, "success");
    });
  }

  async function reset2fa() {
    if (
      !window.confirm(
        `Réinitialiser la 2FA de ${label} ?\n\nLe compte devra ré-enrôler un code à la prochaine connexion.`,
      )
    ) {
      return;
    }
    await run("Réinitialisation 2FA", async () => {
      const updated = await api.admin.reset2fa(user.public_id);
      onUpdated(updated);
      setDetail((d) => (d ? { ...d, totp_enabled: updated.totp_enabled } : d));
      push(`2FA réinitialisée pour ${user.public_id.slice(0, 8)}`, "success");
    });
  }

  async function unlock() {
    await run("Déverrouillage", async () => {
      const updated = await api.admin.unlock(user.public_id);
      setDetail(updated);
      push(`Compte débloqué`, "success");
    });
  }

  async function verifyEmail() {
    await run("Vérification email", async () => {
      const updated = await api.admin.verifyEmail(user.public_id);
      setDetail(updated);
      onUpdated(updated);
      push(`Email marqué comme vérifié`, "success");
    });
  }

  async function sendReset() {
    await run("Envoi du lien", async () => {
      const { sent } = await api.admin.sendPasswordReset(user.public_id);
      push(sent ? "Lien de réinitialisation envoyé" : "Lien généré (envoi SMTP indisponible)", sent ? "success" : "info");
    });
  }

  async function logoutAll() {
    if (!window.confirm(`Déconnecter toutes les sessions de ${label} ?`)) return;
    await run("Déconnexion", async () => {
      const { revoked } = await api.admin.logoutAll(user.public_id);
      setSessions([]);
      push(`${revoked} session(s) révoquée(s)`, "success");
    });
  }

  async function deleteUser() {
    const typed = window.prompt(
      `Suppression DÉFINITIVE de ${label} et de toutes ses données.\nTape l'email (ou le public_id) pour confirmer :`,
    );
    if (typed !== label) return;
    await run("Suppression", async () => {
      await api.admin.deleteUser(user.public_id);
      onDeleted(user.public_id);
      push(`Compte ${user.public_id.slice(0, 8)} supprimé`, "success");
    });
  }

  return (
    <li className="py-3">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={toggle}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          )}
          <span className="min-w-0">
            <span className="block truncate text-sm text-fg">{label}</span>
            <span className="block truncate text-xs text-muted">
              {user.display_name || "—"}
              {user.is_bootstrap ? " · propriétaire" : ""}
              {user.totp_enabled ? " · 2FA" : " · sans-2FA"}
            </span>
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          {user.totp_enabled && (
            <button
              type="button"
              onClick={reset2fa}
              disabled={busy !== null}
              aria-label="Réinitialiser la 2FA"
              title="Réinitialiser la 2FA"
              className="grid h-9 w-9 place-items-center rounded-xl text-fg-soft
                         border border-border/[0.06] bg-overlay/[0.03]
                         hover:text-accent hover:border-accent/40 transition-colors disabled:opacity-50"
            >
              <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          )}
          {user.is_bootstrap ? (
            <span className="text-xs text-muted">{user.role}</span>
          ) : (
            <select
              value={user.role}
              disabled={busy !== null}
              onChange={(e) => changeRole(e.target.value)}
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
      </div>

      {expanded && (
        <div className="mt-3 rounded-xl border border-border/[0.06] bg-overlay/[0.02] p-3">
          {detail === null ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Chargement…
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-1.5">
                <Badge ok={detail.email_verified} label="email vérifié" />
                <Badge ok={detail.has_password} label="mot de passe" />
                <Badge ok={detail.totp_enabled} label="2FA" />
                <Badge ok={detail.has_garmin_credentials} label="Garmin" />
                {detail.locked && (
                  <span className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-400">
                    verrouillé ({detail.failed_attempts} échecs)
                  </span>
                )}
              </div>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <dt className="text-muted">Créé</dt>
                <dd className="text-fg-soft">
                  {detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"}
                </dd>
                <dt className="text-muted">Activités</dt>
                <dd className="text-fg-soft">
                  {detail.n_activities}
                  {detail.last_activity_date ? ` · ${detail.last_activity_date}` : ""}
                </dd>
                <dt className="text-muted">Sessions actives</dt>
                <dd className="text-fg-soft">
                  {sessions === null ? "…" : sessions.length}
                  {sessions && sessions[0]?.last_used_at
                    ? ` · vue ${new Date(sessions[0].last_used_at).toLocaleDateString()}`
                    : ""}
                </dd>
                {detail.role === "athlete" && (
                  <>
                    <dt className="text-muted">Coachs</dt>
                    <dd className="text-fg-soft">
                      {detail.coaches.length
                        ? detail.coaches.map((c) => c.email || c.public_id.slice(0, 8)).join(", ")
                        : "—"}
                    </dd>
                  </>
                )}
                {detail.role === "coach" && (
                  <>
                    <dt className="text-muted">Athlètes</dt>
                    <dd className="text-fg-soft">{detail.athletes_count}</dd>
                  </>
                )}
                {detail.locked && detail.locked_until && (
                  <>
                    <dt className="text-muted">Verrouillé jusqu'à</dt>
                    <dd className="text-fg-soft">
                      {new Date(detail.locked_until).toLocaleString()}
                    </dd>
                  </>
                )}
              </dl>

              <div className="flex flex-wrap gap-1.5">
                {detail.locked && (
                  <ActionButton
                    icon={<Unlock className="h-3.5 w-3.5" />}
                    label="Déverrouiller"
                    onClick={unlock}
                    busy={busy === "Déverrouillage"}
                  />
                )}
                {!detail.email_verified && (
                  <ActionButton
                    icon={<MailCheck className="h-3.5 w-3.5" />}
                    label="Vérifier l'email"
                    onClick={verifyEmail}
                    busy={busy === "Vérification email"}
                  />
                )}
                {detail.email && (
                  <ActionButton
                    icon={<KeyRound className="h-3.5 w-3.5" />}
                    label="Envoyer un reset mdp"
                    onClick={sendReset}
                    busy={busy === "Envoi du lien"}
                  />
                )}
                <ActionButton
                  icon={<LogOut className="h-3.5 w-3.5" />}
                  label="Déconnecter partout"
                  onClick={logoutAll}
                  busy={busy === "Déconnexion"}
                />
                {!detail.is_bootstrap && (
                  <ActionButton
                    icon={<Trash2 className="h-3.5 w-3.5" />}
                    label="Supprimer"
                    onClick={deleteUser}
                    busy={busy === "Suppression"}
                    danger
                  />
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
