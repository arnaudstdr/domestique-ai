import { useEffect, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { api, ApiError } from "../api/client";
import AdminUserRow from "../components/AdminUserRow";
import type { AdminFeedback, AdminSettings, AdminUser, FeedbackStatus } from "../api/types";
import { useToast } from "../hooks/useToast";

const FEEDBACK_STATUSES: FeedbackStatus[] = ["new", "acknowledged", "done", "rejected"];
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
  const [savingSettings, setSavingSettings] = useState(false);
  const [feedbackFilter, setFeedbackFilter] = useState<FeedbackStatus | "all">("all");
  const [savingFeedbackId, setSavingFeedbackId] = useState<number | null>(null);

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
              <AdminUserRow key={u.public_id} user={u} onUpdated={updateUser} onDeleted={removeUser} />
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
    </div>
  );
}
