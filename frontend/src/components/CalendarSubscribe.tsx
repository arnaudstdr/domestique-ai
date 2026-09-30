import { useEffect, useState } from "react";
import {
  CalendarPlus,
  Check,
  ChevronDown,
  Copy,
  Info,
  RefreshCw,
  Smartphone,
} from "lucide-react";
import { ApiError, api } from "../api/client";
import type { SubscriptionFeed } from "../api/types";
import { useToast } from "../hooks/useToast";
import { useViewing } from "../hooks/useViewing";

/**
 * Carte d'abonnement au calendrier : URL du flux iCalendar de l'athlète
 * (token par athlète), boutons Apple/Google, QR et lien copiable.
 *
 * Le flux se met à jour tout seul (le calendrier poll l'URL) : la fenêtre
 * évolue après chaque revue hebdo sans doublons (UID stables).
 *
 * Repliable et fermée par défaut : c'est un réglage one-time sur une page
 * d'action, le flux n'est chargé qu'à la première ouverture.
 */
export default function CalendarSubscribe() {
  const [open, setOpen] = useState(false);
  const [feed, setFeed] = useState<SubscriptionFeed | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [rotating, setRotating] = useState(false);
  const { push } = useToast();
  const viewing = useViewing();

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    api.plan
      .subscription()
      .then((res) => {
        if (alive) {
          setFeed(res);
          setError(null);
        }
      })
      .catch((err) => {
        if (!alive) return;
        setError(
          err instanceof ApiError ? err.message : "Abonnement indisponible.",
        );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, viewing?.id]);

  async function copyUrl() {
    if (!feed?.url) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(feed.url);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
        return;
      }
    } catch {
      // clipboard indisponible → sélection manuelle
    }
    push("Copie impossible — sélectionne l'URL manuellement.", "error");
  }

  async function rotate() {
    if (
      !window.confirm(
        "Régénérer le lien ? L'abonnement actuel devra être reconfiguré sur ton calendrier.",
      )
    ) {
      return;
    }
    setRotating(true);
    try {
      const res = await api.plan.rotateSubscription();
      setFeed(res);
      setCopied(false);
      push("Nouveau lien généré.", "success");
    } catch (err) {
      push(
        err instanceof ApiError ? err.message : "Échec de la régénération.",
        "error",
      );
    } finally {
      setRotating(false);
    }
  }

  return (
    <div className="card space-y-4">
      <h2 className="font-display text-lg font-bold tracking-tight">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="calendar-subscribe-body"
          className="flex w-full items-center justify-between gap-2 text-left"
        >
          <span className="flex items-center gap-2">
            <CalendarPlus
              className="h-4 w-4 text-accent"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            Abonnement calendrier
          </span>
          <span className="flex items-center gap-2">
            {viewing && (
              <span className="pill bg-accent/15 text-accent">athlète</span>
            )}
            <ChevronDown
              className={`h-4 w-4 text-muted transition-transform ${
                open ? "rotate-180" : ""
              }`}
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </span>
        </button>
      </h2>

      {open && (
        <div id="calendar-subscribe-body" className="space-y-4">
          <p className="text-sm text-fg-soft">
            Ajoute tes séances à Apple Calendrier ou Google Calendar : elles se
            mettent à jour automatiquement après chaque adaptation du plan.
          </p>

          {loading && <p className="text-sm text-muted">Chargement du lien…</p>}

          {!loading && error && (
            <div className="rounded-lg border border-border/10 bg-overlay/[0.03] p-3 text-sm text-muted">
              {error}
            </div>
          )}

          {!loading && feed && (
            <div className="space-y-4">
              <div className="flex gap-2">
                <input
                  readOnly
                  value={feed.url}
                  onFocus={(e) => e.currentTarget.select()}
                  aria-label="URL d'abonnement iCalendar"
                  className="input flex-1 font-mono text-xs"
                />
                <button
                  type="button"
                  onClick={copyUrl}
                  className="btn-ghost shrink-0 px-3"
                  aria-label="Copier l'URL du calendrier"
                  title="Copier l'URL"
                >
                  {copied ? (
                    <Check
                      className="h-4 w-4 text-accent"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                  ) : (
                    <Copy
                      className="h-4 w-4"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                  )}
                </button>
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                <a
                  href={feed.webcal_url}
                  className="btn-primary w-full"
                  title="Ajouter à Apple Calendrier"
                >
                  <Smartphone
                    className="h-4 w-4"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  Apple Calendrier
                </a>
                <a
                  href={feed.google_url}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-ghost w-full"
                  title="Ajouter à Google Calendar"
                >
                  <CalendarPlus
                    className="h-4 w-4"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  Google Calendar
                </a>
              </div>

              <div className="flex flex-col items-center gap-4 rounded-xl border border-border/[0.06] bg-overlay/[0.02] p-3 sm:flex-row sm:items-start">
                <img
                  src={feed.qr_svg_data_uri}
                  alt="QR code du flux calendrier"
                  className="h-40 w-40 shrink-0 rounded-lg bg-white p-2"
                />
                <div className="space-y-2 text-xs text-muted">
                  <p className="flex items-center gap-1.5 text-fg-soft">
                    <Info
                      className="h-3.5 w-3.5"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                    Scanner depuis un autre appareil
                  </p>
                  <details className="group">
                    <summary className="cursor-pointer text-fg-soft hover:text-accent">
                      Comment ajouter le flux ?
                    </summary>
                    <ul className="mt-1.5 ml-3 list-disc space-y-1">
                      <li>
                        <span className="text-fg-soft">Apple :</span> bouton
                        ci-dessus, ou Fichier → Nouvel abonnement à un calendrier
                        → colle l'URL.
                      </li>
                      <li>
                        <span className="text-fg-soft">Google :</span> Autres
                        agendas → À partir d'une URL → colle l'URL.
                      </li>
                      <li>
                        <span className="text-fg-soft">Outlook / autres :</span>{" "}
                        abonne-toi à partir de l'URL du flux (le champ ci-dessus).
                      </li>
                    </ul>
                  </details>
                </div>
              </div>

              {!viewing && (
                <button
                  type="button"
                  onClick={rotate}
                  disabled={rotating}
                  className="inline-flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-red-300 disabled:opacity-50"
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${rotating ? "animate-spin" : ""}`}
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  {rotating
                    ? "Régénération…"
                    : "Régénérer le lien (révoque l'ancien)"}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
