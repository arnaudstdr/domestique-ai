import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ChevronDown,
  KeyRound,
  Loader2,
  LogOut,
  MailPlus,
  MailX,
  MessageSquare,
  RefreshCw,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  Trash2,
  Unlock,
  UserCog,
  UserRoundCheck,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { AdminAuditEntry } from "../api/types";
import { useToast } from "../hooks/useToast";
import FilterTab from "./FilterTab";

type AuditPeriod = "24h" | "7d" | "30d" | "all";

const AUDIT_FAMILIES: { key: string; label: string; actions: string[] }[] = [
  {
    key: "accounts",
    label: "Comptes",
    actions: ["role_change", "delete_account", "unlock_account", "verify_email"],
  },
  { key: "security", label: "Sécurité", actions: ["reset_2fa", "password_reset", "logout_all"] },
  {
    key: "invitations",
    label: "Invitations",
    actions: ["invitation_create", "invitation_revoke"],
  },
  { key: "feedback", label: "Retours", actions: ["feedback_status"] },
  { key: "platform", label: "Plateforme", actions: ["settings_update", "purge_orphan_spaces"] },
];

const NODE_STYLES = {
  ctl: "border-ctl/30 bg-ctl/10 text-ctl",
  amber: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  accent: "border-accent/30 bg-accent/10 text-accent",
  green: "border-green-500/30 bg-green-500/10 text-green-400",
  muted: "border-border/[0.12] bg-overlay/[0.04] text-muted",
  red: "border-red-500/30 bg-red-500/10 text-red-400",
} as const;

type NodeColor = keyof typeof NODE_STYLES;

const AUDIT_META: Record<
  string,
  { label: string; icon: typeof Activity; color: NodeColor }
> = {
  role_change: { label: "Changement de rôle", icon: UserCog, color: "ctl" },
  delete_account: { label: "Suppression de compte", icon: Trash2, color: "red" },
  unlock_account: { label: "Déverrouillage de compte", icon: Unlock, color: "ctl" },
  verify_email: { label: "Email vérifié", icon: UserRoundCheck, color: "green" },
  reset_2fa: { label: "Réinitialisation 2FA", icon: ShieldAlert, color: "amber" },
  password_reset: { label: "Lien de reset mot de passe", icon: KeyRound, color: "amber" },
  logout_all: { label: "Déconnexion globale", icon: LogOut, color: "amber" },
  invitation_create: { label: "Création d'invitation", icon: MailPlus, color: "accent" },
  invitation_revoke: { label: "Révocation d'invitation", icon: MailX, color: "accent" },
  feedback_status: { label: "Statut d'un retour", icon: MessageSquare, color: "green" },
  settings_update: { label: "Réglages plateforme", icon: Settings, color: "muted" },
  purge_orphan_spaces: { label: "Nettoyage d'espaces orphelins", icon: Sparkles, color: "muted" },
};

const FEEDBACK_LABELS: Record<string, string> = {
  new: "Nouveau",
  acknowledged: "Pris en compte",
  done: "Fait",
  rejected: "Rejeté",
};

const PERIODS: { key: AuditPeriod; label: string }[] = [
  { key: "24h", label: "24 h" },
  { key: "7d", label: "7 j" },
  { key: "30d", label: "30 j" },
  { key: "all", label: "Tout" },
];

const PAGE_SIZE = 25;
const RTF = new Intl.RelativeTimeFormat("fr-FR", { numeric: "auto" });

function errMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function shortId(publicId: string | null): string | null {
  return publicId ? publicId.slice(0, 8) : null;
}

function entrySummary(entry: AdminAuditEntry): string | null {
  const d = asRecord(entry.details);
  if (!d) return null;
  switch (entry.action) {
    case "role_change":
      return d.from && d.to ? `${String(d.from)} → ${String(d.to)}` : null;
    case "feedback_status":
      return typeof d.feedback_id === "number" && typeof d.status === "string"
        ? `retour #${d.feedback_id} → ${FEEDBACK_LABELS[d.status] ?? d.status}`
        : null;
    case "settings_update": {
      const fields = Object.keys(d);
      return fields.length > 0 ? `champs : ${fields.join(", ")}` : null;
    }
    case "invitation_create":
      return typeof d.invitation_id === "number"
        ? `invitation #${d.invitation_id} (${String(d.role)})`
        : null;
    case "invitation_revoke":
      return typeof d.invitation_id === "number" ? `invitation #${d.invitation_id}` : null;
    case "purge_orphan_spaces":
      return typeof d.removed === "number" ? `${d.removed} espace(s) supprimé(s)` : null;
    case "password_reset":
      return d.sent === true ? "email envoyé" : d.sent === false ? "envoi échoué" : null;
    case "logout_all":
      return typeof d.revoked === "number" ? `${d.revoked} session(s) révoquée(s)` : null;
    case "delete_account":
      return typeof d.email === "string" ? d.email : null;
    default:
      return null;
  }
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function dayLabel(iso: string): string {
  const date = new Date(iso);
  const diffDays = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86_400_000);
  if (diffDays === 0) return "Aujourd'hui";
  if (diffDays === 1) return "Hier";
  return date.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

function timeAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 60) return RTF.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 24) return RTF.format(-hours, "hour");
  const days = Math.round(hours / 24);
  if (days < 30) return RTF.format(-days, "day");
  return new Date(iso).toLocaleDateString("fr-FR");
}

function buildDays(entries: AdminAuditEntry[]) {
  const days: { key: string; label: string; items: AdminAuditEntry[] }[] = [];
  for (const entry of entries) {
    const date = new Date(entry.created_at);
    const key = dayKey(date);
    const last = days[days.length - 1];
    if (last && last.key === key) last.items.push(entry);
    else days.push({ key, label: dayLabel(entry.created_at), items: [entry] });
  }
  return days;
}

function AuditItem({
  entry,
  last,
  expanded,
  onToggle,
}: {
  entry: AdminAuditEntry;
  last: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = AUDIT_META[entry.action] ?? {
    label: entry.action,
    icon: Activity,
    color: "muted" as NodeColor,
  };
  const Icon = meta.icon;
  const details = asRecord(entry.details);
  const summary = entrySummary(entry);
  const actor = entry.actor_label ?? shortId(entry.actor_public_id) ?? "—";
  const target = entry.target_label ?? shortId(entry.target_public_id);

  return (
    <li className="relative flex gap-3">
      <div className="relative flex w-6 shrink-0 justify-center">
        {!last && (
          <span
            className="absolute bottom-0 left-1/2 top-6 w-px -translate-x-1/2 bg-border/[0.12]"
            aria-hidden="true"
          />
        )}
        <span
          className={`z-10 flex h-6 w-6 items-center justify-center rounded-full border ${NODE_STYLES[meta.color]}`}
        >
          <Icon className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
        </span>
      </div>
      <div className="min-w-0 flex-1 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-xs text-fg-soft">{meta.label}</span>
          <span
            className="shrink-0 text-[11px] text-muted"
            title={new Date(entry.created_at).toLocaleString()}
          >
            {timeAgo(entry.created_at)}
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-[11px] text-muted">
          {summary && <span className="text-fg-soft">{summary}</span>}
          <span>
            {summary ? "· " : ""}par {actor}
          </span>
          {target && <span>→ {target}</span>}
        </div>
        {details && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="mt-1 flex items-center gap-1 text-[11px] text-muted transition-colors hover:text-accent"
          >
            Détails
            <ChevronDown
              className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`}
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </button>
        )}
        {details && expanded && (
          <dl className="mt-1.5 space-y-0.5 rounded-lg border border-border/[0.06] bg-overlay/[0.02] px-2 py-1.5 text-[11px]">
            {Object.entries(details).map(([field, value]) => (
              <div key={field} className="flex gap-2">
                <dt className="shrink-0 text-muted">{field}</dt>
                <dd className="min-w-0 break-all text-fg-soft">
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </li>
  );
}

export default function AdminAuditLog() {
  const { push } = useToast();
  const [entries, setEntries] = useState<AdminAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [familyKey, setFamilyKey] = useState("all");
  const [period, setPeriod] = useState<AuditPeriod>("all");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const actions = useMemo(
    () =>
      familyKey === "all"
        ? undefined
        : AUDIT_FAMILIES.find((f) => f.key === familyKey)?.actions,
    [familyKey],
  );

  const load = useCallback(
    async (beforeId?: number) => {
      const id = ++requestId.current;
      try {
        const page = await api.admin.audit({
          limit: PAGE_SIZE,
          beforeId,
          actions,
          q: search || undefined,
          period,
        });
        return id === requestId.current ? page : null;
      } catch (err) {
        if (id === requestId.current) push(`Journal : ${errMessage(err)}`, "error");
        return null;
      }
    },
    [actions, period, push, search],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    const page = await load();
    setLoading(false);
    if (page) {
      setEntries(page);
      setHasMore(page.length === PAGE_SIZE);
      setExpandedId(null);
    }
  }, [load]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function loadMore() {
    if (loading || loadingMore || !hasMore || entries.length === 0) return;
    setLoadingMore(true);
    const page = await load(entries[entries.length - 1].id);
    setLoadingMore(false);
    if (!page) return;
    setEntries((prev) => [...prev, ...page]);
    setHasMore(page.length === PAGE_SIZE);
  }

  const days = useMemo(() => buildDays(entries), [entries]);

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">Journal d'audit</h3>
        <button
          type="button"
          onClick={() => void reload()}
          disabled={loading}
          aria-label="Rafraîchir le journal"
          className="rounded-lg border border-border/[0.06] bg-overlay/[0.03] p-1.5 text-muted transition-colors hover:text-accent disabled:opacity-50"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <FilterTab active={familyKey === "all"} label="Tout" onClick={() => setFamilyKey("all")} />
        {AUDIT_FAMILIES.map((family) => (
          <FilterTab
            key={family.key}
            active={familyKey === family.key}
            label={family.label}
            onClick={() => setFamilyKey(family.key)}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {PERIODS.map((p) => (
          <FilterTab
            key={p.key}
            active={period === p.key}
            label={p.label}
            onClick={() => setPeriod(p.key)}
          />
        ))}
        <label className="relative w-full sm:ml-auto sm:w-56">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un compte…"
            aria-label="Rechercher un compte (nom, email, id)"
            className="input py-1 pl-7 text-xs"
          />
        </label>
      </div>
      {loading && entries.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Chargement…
        </div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted">
          {search ? "Aucune action pour cette recherche." : "Aucune action sur cette période."}
        </p>
      ) : (
        <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <ol className="space-y-4">
            {days.map((day) => (
              <li key={day.key}>
                <div className="mb-2 flex items-center gap-2 text-[11px] text-muted">
                  <span className="font-semibold uppercase tracking-wide">{day.label}</span>
                  <span>· {day.items.length}</span>
                </div>
                <ol>
                  {day.items.map((entry, index) => (
                    <AuditItem
                      key={entry.id}
                      entry={entry}
                      last={index === day.items.length - 1}
                      expanded={expandedId === entry.id}
                      onToggle={() =>
                        setExpandedId((prev) => (prev === entry.id ? null : entry.id))
                      }
                    />
                  ))}
                </ol>
              </li>
            ))}
          </ol>
          {hasMore && (
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loading || loadingMore}
              className="mt-1 w-full rounded-lg border border-border/[0.06] bg-overlay/[0.03] py-2 text-xs text-fg-soft transition-colors hover:text-accent disabled:opacity-50"
            >
              {loadingMore ? "Chargement…" : "Afficher plus"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
