import { useEffect, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { api, ApiError } from "../api/client";
import AdminUserRow from "../components/AdminUserRow";
import type {
  AdminAuditEntry,
  AdminFeedback,
  AdminInvitation,
  AdminSettings,
  AdminStats,
  AdminStatus,
  AdminUser,
  FeedbackStatus,
} from "../api/types";
import { useToast } from "../hooks/useToast";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} o`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} Ko`;
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`;
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/[0.06] bg-overlay/[0.02] px-3 py-2">
      <div className="text-lg font-semibold text-fg">{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
      {sub && <div className="text-[11px] text-amber-400">{sub}</div>}
    </div>
  );
}

const FEEDBACK_STATUSES: FeedbackStatus[] = ["new", "acknowledged", "done", "rejected"];

const INVITATION_STATUS_LABELS: Record<string, string> = {
  pending: "En attente",
  accepted: "Acceptée",
  revoked: "Révoquée",
  expired: "Expirée",
};

const AUDIT_LABELS: Record<string, string> = {
  role_change: "Changement de rôle",
  reset_2fa: "Réinitialisation 2FA",
  unlock_account: "Déverrouillage de compte",
  verify_email: "Email vérifié",
  password_reset: "Lien de reset mot de passe",
  logout_all: "Déconnexion globale",
  delete_account: "Suppression de compte",
  feedback_status: "Statut d'un retour",
  settings_update: "Réglages plateforme",
  invitation_revoke: "Révocation d'invitation",
  purge_orphan_spaces: "Nettoyage d'espaces orphelins",
};
const FEEDBACK_LABELS: Record<FeedbackStatus, string> = {
  new: "Nouveau",
  acknowledged: "Pris en compte",
  done: "Fait",
  rejected: "Rejeté",
};
const FEEDBACK_BADGE: Record<FeedbackStatus, string> = {
  new: "bg-accent/15 text-accent border-accent/30",
  acknowledged: "bg-ctl/15 text-ctl border-ctl/30",
  done: "bg-green-500/15 text-green-400 border-green-500/30",
  rejected: "bg-red-500/15 text-red-400 border-red-500/30",
};

function FilterTab({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1 text-xs transition-colors ${
        active
          ? "border-accent/40 bg-accent/15 text-accent"
          : "border-border/[0.06] bg-overlay/[0.03] text-fg-soft hover:text-accent"
      }`}
    >
      {label}
    </button>
  );
}

function errMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

export default function Admin() {
  const { push } = useToast();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [feedback, setFeedback] = useState<AdminFeedback[] | null>(null);
  const [audit, setAudit] = useState<AdminAuditEntry[] | null>(null);
  const [invitations, setInvitations] = useState<AdminInvitation[] | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [purging, setPurging] = useState(false);
  const [broadcastDraft, setBroadcastDraft] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<FeedbackStatus | "all">("all");
  const [savingFeedbackId, setSavingFeedbackId] = useState<number | null>(null);

  useEffect(() => {
    api.admin.users().then(setUsers).catch((e) => push(`Comptes : ${errMessage(e)}`, "error"));
    api.admin
      .settings()
      .then((s) => {
        setSettings(s);
        setBroadcastDraft(s.broadcast_message ?? "");
      })
      .catch((e) => push(`Réglages : ${errMessage(e)}`, "error"));
    api.admin
      .feedback()
      .then(setFeedback)
      .catch((e) => push(`Retours : ${errMessage(e)}`, "error"));
    api.admin
      .audit()
      .then(setAudit)
      .catch((e) => push(`Journal : ${errMessage(e)}`, "error"));
    api.admin
      .invitations()
      .then(setInvitations)
      .catch((e) => push(`Invitations : ${errMessage(e)}`, "error"));
    api.admin
      .stats()
      .then(setStats)
      .catch((e) => push(`Stats : ${errMessage(e)}`, "error"));
    api.admin
      .status()
      .then(setStatus)
      .catch((e) => push(`Statut : ${errMessage(e)}`, "error"));
  }, [push]);

  function updateUser(updated: AdminUser) {
    setUsers((prev) =>
      prev ? prev.map((u) => (u.public_id === updated.public_id ? updated : u)) : prev,
    );
  }

  function removeUser(publicId: string) {
    setUsers((prev) => (prev ? prev.filter((u) => u.public_id !== publicId) : prev));
  }

  async function changeFeedbackStatus(f: AdminFeedback, status: FeedbackStatus) {
    setSavingFeedbackId(f.id);
    try {
      const updated = await api.admin.setFeedbackStatus(f.id, status);
      setFeedback((prev) => (prev ? prev.map((x) => (x.id === updated.id ? updated : x)) : prev));
      push(`Retour #${updated.id} : ${FEEDBACK_LABELS[updated.status]}`, "success");
    } catch (err) {
      push(`Statut du retour : ${errMessage(err)}`, "error");
    } finally {
      setSavingFeedbackId(null);
    }
  }

  async function revokeInvitation(inv: AdminInvitation) {
    if (!window.confirm(`Révoquer l'invitation #${inv.id} ?`)) return;
    try {
      await api.admin.revokeInvitation(inv.id);
      setInvitations((prev) =>
        prev ? prev.map((x) => (x.id === inv.id ? { ...x, status: "revoked" } : x)) : prev,
      );
      push(`Invitation #${inv.id} révoquée`, "success");
    } catch (err) {
      push(`Révocation : ${errMessage(err)}`, "error");
    }
  }

  async function purgeOrphans() {
    const n = stats?.orphan_athlete_spaces ?? 0;
    if (
      !window.confirm(
        `Supprimer ${n} dossier(s) athlète orphelin(s) (sans compte correspondant) ?\n\nIls ne sont jamais lus par l'app.`,
      )
    ) {
      return;
    }
    setPurging(true);
    try {
      const { removed } = await api.admin.purgeOrphanSpaces();
      push(`${removed} dossier(s) orphelin(s) supprimé(s)`, "success");
      setStats(await api.admin.stats());
    } catch (err) {
      push(`Nettoyage : ${errMessage(err)}`, "error");
    } finally {
      setPurging(false);
    }
  }

  async function saveSettings(patch: Partial<AdminSettings>) {
    setSavingSettings(true);
    try {
      const updated = await api.admin.updateSettings(patch);
      setSettings(updated);
      setBroadcastDraft(updated.broadcast_message ?? "");
      push("Réglages enregistrés", "success");
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
        <h3 className="text-sm font-semibold text-fg">Plateforme</h3>
        {stats === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Comptes" value={Object.values(stats.users_by_role).reduce((a, b) => a + b, 0)} />
              <Stat label="Sessions actives" value={stats.active_sessions} />
              <Stat label="Garmin connectés" value={stats.garmin_connected} />
              <Stat
                label="Espaces athlètes"
                value={stats.athlete_spaces}
                sub={
                  stats.orphan_athlete_spaces > 0
                    ? `dont ${stats.orphan_athlete_spaces} orphelins`
                    : undefined
                }
              />
            </div>
            {stats.orphan_athlete_spaces > 0 && (
              <button
                type="button"
                onClick={purgeOrphans}
                disabled={purging}
                className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-2.5 py-1.5 text-xs text-amber-400 hover:border-amber-500/50 disabled:opacity-50"
              >
                Nettoyer les {stats.orphan_athlete_spaces} dossiers orphelins
              </button>
            )}
            <div className="flex flex-wrap gap-3 text-xs text-muted">
              {Object.entries(stats.users_by_role).map(([role, n]) => (
                <span key={role}>
                  {role} : <span className="text-fg-soft">{n}</span>
                </span>
              ))}
              <span>DB : <span className="text-fg-soft">{formatBytes(stats.platform_db_bytes)}</span></span>
            </div>
          </div>
        )}
        {status && (
          <div className="space-y-1 border-t border-border/[0.06] pt-3 text-xs text-muted">
            <div>
              Version <span className="text-fg-soft">{status.version}</span> ·{" "}
              scheduler{" "}
              <span className={status.scheduler_running ? "text-green-400" : "text-red-400"}>
                {status.scheduler_running ? "actif" : "arrêté"}
              </span>{" "}
              ({status.jobs.length} jobs)
            </div>
            <div>
              Sync Garmin : {status.garmin_syncing} en cours, {status.garmin_errors} erreur(s)
              {status.garmin_last_finished_at
                ? ` · dernière ${new Date(status.garmin_last_finished_at).toLocaleString()}`
                : ""}
            </div>
            <div>
              Healthcheck :{" "}
              {status.healthcheck_configured
                ? status.healthcheck_last
                  ? `${status.healthcheck_last.ok ? "ok" : "échec"} (${new Date(status.healthcheck_last.at).toLocaleString()})`
                  : "configuré, jamais pingé"
                : "non configuré"}
            </div>
            <div>
              Fuseau {status.timezone}
              {status.daily_check ? ` · check matin ${status.daily_check}` : ""}
              {status.weekly_review ? ` · revue hebdo ${status.weekly_review}` : ""}
            </div>
          </div>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Réglages plateforme</h3>
        {settings === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : (
          <div className="space-y-4">
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
                onChange={(e) => saveSettings({ signup_enabled: e.target.checked })}
                className="h-5 w-5 shrink-0"
              />
            </label>
            <label className="flex items-center justify-between gap-4">
              <span className="text-sm text-fg-soft">
                Mode maintenance
                <span className="block text-xs text-muted">
                  Affiche un bandeau « maintenance » à tous (non bloquant).
                </span>
              </span>
              <input
                type="checkbox"
                checked={settings.maintenance_mode}
                disabled={savingSettings}
                onChange={(e) => saveSettings({ maintenance_mode: e.target.checked })}
                className="h-5 w-5 shrink-0"
              />
            </label>
            <label className="block">
              <span className="text-sm text-fg-soft">Message diffusé (bandeau)</span>
              <textarea
                value={broadcastDraft}
                onChange={(e) => setBroadcastDraft(e.target.value)}
                rows={3}
                maxLength={500}
                placeholder="Laisser vide pour retirer le bandeau."
                className="input mt-1 resize-none"
              />
            </label>
            <button
              type="button"
              onClick={() => saveSettings({ broadcast_message: broadcastDraft })}
              disabled={savingSettings}
              className="btn-primary disabled:opacity-50"
            >
              Publier le message
            </button>
          </div>
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
              <AdminUserRow key={u.public_id} user={u} onUpdated={updateUser} onDeleted={removeUser} />
            ))}
          </ul>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Invitations</h3>
        {invitations === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : invitations.length === 0 ? (
          <p className="text-sm text-muted">Aucune invitation.</p>
        ) : (
          <ul className="divide-y divide-border/[0.06]">
            {invitations.map((inv) => (
              <li key={inv.id} className="flex items-center justify-between gap-3 py-2 text-xs">
                <div className="min-w-0">
                  <div className="text-fg-soft">
                    #{inv.id} · {inv.role} ·{" "}
                    {INVITATION_STATUS_LABELS[inv.status] ?? inv.status}
                  </div>
                  <div className="truncate text-muted">
                    {inv.created_by_email || inv.created_by_public_id?.slice(0, 8) || "—"}
                    {inv.created_at ? ` · ${new Date(inv.created_at).toLocaleDateString()}` : ""}
                  </div>
                </div>
                {inv.status === "pending" && (
                  <button
                    type="button"
                    onClick={() => revokeInvitation(inv)}
                    className="shrink-0 rounded-lg border border-red-500/30 bg-red-500/[0.06] px-2.5 py-1 text-red-400 hover:border-red-500/50"
                  >
                    Révoquer
                  </button>
                )}
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
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              <FilterTab
                active={feedbackFilter === "all"}
                label={`Tous (${feedback.length})`}
                onClick={() => setFeedbackFilter("all")}
              />
              {FEEDBACK_STATUSES.map((s) => (
                <FilterTab
                  key={s}
                  active={feedbackFilter === s}
                  label={`${FEEDBACK_LABELS[s]} (${feedback.filter((f) => f.status === s).length})`}
                  onClick={() => setFeedbackFilter(s)}
                />
              ))}
            </div>
            {(() => {
              const shown =
                feedbackFilter === "all"
                  ? feedback
                  : feedback.filter((f) => f.status === feedbackFilter);
              if (shown.length === 0) {
                return <p className="text-sm text-muted">Aucun retour dans ce filtre.</p>;
              }
              return (
                <ul className="space-y-3">
                  {shown.map((f) => (
                    <li key={f.id} className="rounded-xl border border-border/[0.06] p-3">
                      <div className="flex items-center justify-between gap-2 text-xs text-muted">
                        <span className="font-semibold text-accent">{f.category}</span>
                        <span>{new Date(f.created_at).toLocaleString()}</span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-fg-soft">{f.message}</p>
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs text-muted">
                          {f.author_email || f.public_id || "anonyme"}
                          {f.page ? ` · ${f.page}` : ""}
                        </span>
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded-lg border px-2 py-0.5 text-xs ${FEEDBACK_BADGE[f.status]}`}
                          >
                            {FEEDBACK_LABELS[f.status]}
                          </span>
                          <select
                            value={f.status}
                            disabled={savingFeedbackId === f.id}
                            onChange={(e) =>
                              changeFeedbackStatus(f, e.target.value as FeedbackStatus)
                            }
                            aria-label={`Statut du retour ${f.id}`}
                            className="input w-auto py-1 text-xs disabled:opacity-50"
                          >
                            {FEEDBACK_STATUSES.map((s) => (
                              <option key={s} value={s}>
                                {FEEDBACK_LABELS[s]}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              );
            })()}
          </>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-fg">Journal d'audit</h3>
        {audit === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Chargement…
          </div>
        ) : audit.length === 0 ? (
          <p className="text-sm text-muted">Aucune action enregistrée.</p>
        ) : (
          <ul className="divide-y divide-border/[0.06]">
            {audit.map((e) => (
              <li key={e.id} className="flex items-start justify-between gap-3 py-2 text-xs">
                <div className="min-w-0">
                  <div className="text-fg-soft">{AUDIT_LABELS[e.action] ?? e.action}</div>
                  <div className="truncate text-muted">
                    {e.actor_public_id ? `par ${e.actor_public_id.slice(0, 8)}` : "—"}
                    {e.target_public_id ? ` → ${e.target_public_id.slice(0, 8)}` : ""}
                  </div>
                </div>
                <span className="shrink-0 text-muted">
                  {new Date(e.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
