import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  Check,
  Copy,
  Eye,
  Link2,
  Plus,
  Users,
} from "lucide-react";
import { api, ApiError, setViewingAthlete } from "../api/client";
import type { AthleteSummary, InvitationCreated, InvitationOut } from "../api/types";
import { useToast } from "../hooks/useToast";

export default function Roster() {
  return (
    <div className="stagger space-y-4">
      <header>
        <h2 className="flex items-center gap-2 font-display text-2xl font-extrabold tracking-tight text-gray-50">
          <Users className="h-6 w-6 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Roster
        </h2>
        <p className="text-xs text-muted">
          Tes athlètes et leurs liens d'invitation. Ouvre un athlète pour
          consulter ses données en lecture seule.
        </p>
      </header>
      <AthletesSection />
      <InvitationsSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Athlètes
// ---------------------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return "Aucune activité";
  const day = iso.slice(0, 10);
  return `Dernière activité le ${day}`;
}

function AthletesSection() {
  const [athletes, setAthletes] = useState<AthleteSummary[] | null>(null);
  const navigate = useNavigate();
  const { push } = useToast();

  useEffect(() => {
    api.auth
      .athletes()
      .then(setAthletes)
      .catch((err) => {
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Athlètes : ${msg}`, "error");
        setAthletes([]);
      });
  }, []);

  function consult(a: AthleteSummary) {
    setViewingAthlete(a.public_id, a.display_name);
    navigate("/");
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Users className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Athlètes
      </h3>
      {athletes === null ? (
        <p className="text-xs text-muted">Chargement…</p>
      ) : athletes.length === 0 ? (
        <p className="text-xs text-muted">
          Aucun athlète pour l'instant. Génère un lien d'invitation ci-dessous.
        </p>
      ) : (
        <ul className="space-y-2">
          {athletes.map((a) => (
            <li
              key={a.public_id}
              className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2.5"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-100">
                  {a.display_name || "Sans nom"}
                </p>
                <p className="flex items-center gap-1.5 text-[11px] text-muted">
                  <span
                    className={`pill ${
                      a.strava_connected
                        ? "text-accent"
                        : "text-muted"
                    }`}
                  >
                    <Link2 className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                    {a.strava_connected ? "Strava OK" : "Strava absent"}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Activity className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                    {a.n_activities} act.
                  </span>
                </p>
                <p className="text-[11px] text-muted">{formatDate(a.last_activity_date)}</p>
              </div>
              <button
                type="button"
                onClick={() => consult(a)}
                className="btn-ghost flex shrink-0 items-center gap-1.5 px-3 py-2 text-xs"
              >
                <Eye className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                Consulter
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Invitations
// ---------------------------------------------------------------------------

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // clipboard indisponible (http non sécurisé, permission) → fallback
  }
  try {
    const el = document.createElement("textarea");
    el.value = text;
    el.style.position = "fixed";
    el.style.opacity = "0";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}

function InvitationsSection() {
  const [invitations, setInvitations] = useState<InvitationOut[] | null>(null);
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [generating, setGenerating] = useState(false);
  const { push } = useToast();

  function refresh() {
    api.auth
      .listInvitations()
      .then(setInvitations)
      .catch(() => setInvitations([]));
  }

  useEffect(refresh, []);

  const inviteUrl = created
    ? `${window.location.origin}${created.invite_url}`
    : null;

  async function generate() {
    setGenerating(true);
    try {
      const inv = await api.auth.createInvitation("athlete");
      setCreated(inv);
      refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Invitation : ${msg}`, "error");
    } finally {
      setGenerating(false);
    }
  }

  async function copy() {
    if (!inviteUrl) return;
    const ok = await copyToClipboard(inviteUrl);
    push(ok ? "Lien copié." : "Copie impossible — sélectionne le lien.", ok ? "success" : "error");
  }

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Plus className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Inviter un athlète
      </h3>
      <p className="text-xs text-muted">
        Génère un lien à usage unique. L'athlète crée son compte en l'ouvrant,
        puis connecte son Strava.
      </p>
      <button
        type="button"
        onClick={generate}
        disabled={generating}
        className="btn-primary w-full"
      >
        {generating ? "Génération…" : "Générer un lien d'invitation"}
      </button>

      {inviteUrl && (
        <div className="space-y-2 rounded-xl border border-accent/30 bg-accent/[0.06] p-3">
          <p className="label-eyebrow">Lien à partager (usage unique)</p>
          <div className="flex items-center gap-2">
            <input className="input flex-1 font-mono text-xs" readOnly value={inviteUrl} />
            <button
              type="button"
              onClick={copy}
              aria-label="Copier le lien"
              className="btn-ghost flex shrink-0 items-center gap-1.5 px-3 py-2 text-xs"
            >
              <Copy className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Copier
            </button>
          </div>
        </div>
      )}

      {invitations && invitations.length > 0 && (
        <ul className="space-y-1.5 pt-1">
          {invitations.map((inv, i) => (
            <li
              key={`${inv.created_at}-${i}`}
              className="flex items-center justify-between gap-2 text-[11px] text-muted"
            >
              <span>
                {inv.role} · {inv.created_at.slice(0, 10)}
              </span>
              <span
                className={`pill ${
                  inv.status === "accepted" ? "text-accent" : "text-muted"
                }`}
              >
                {inv.status === "accepted" ? (
                  <Check className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                ) : null}
                {inv.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
