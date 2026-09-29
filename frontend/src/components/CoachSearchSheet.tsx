import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Brain, Search, X } from "lucide-react";
import { api } from "../api/client";
import type { CoachSearchHit } from "../api/types";
import { useToast } from "../hooks/useToast";

const SEARCH_DEBOUNCE_MS = 300;

const SOURCE_LABELS: Record<string, string> = {
  message: "Message",
  summary: "Résumé",
  fact: "Mémoire",
};

interface Props {
  open: boolean;
  onClose: () => void;
  onJump: (messageId: number) => void;
}

export default function CoachSearchSheet({ open, onClose, onJump }: Props) {
  const navigate = useNavigate();
  const { push } = useToast();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<CoachSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Incrémenté à chaque requête : seules les réponses de la dernière lancée
  // sont prises en compte (évite qu'une réponse lente écrase une plus récente).
  const requestIdRef = useRef(0);

  // On repart d'une recherche vierge à chaque ouverture et on focus le champ.
  useEffect(() => {
    if (!open) return;
    requestIdRef.current += 1;
    setQuery("");
    setHits([]);
    setSearching(false);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 50);
    return () => window.clearTimeout(timer);
  }, [open]);

  // Escape pour fermer + lock du scroll de la page derrière le sheet.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  // Recherche live, débouncée. Les hits précédents restent affichés pendant la
  // frappe, seul l'état `searching` signale le rafraîchissement.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (!q) {
      // Invalide une éventuelle requête en vol pour que sa réponse ne
      // réaffiche pas des hits après un effacement du champ.
      requestIdRef.current += 1;
      setHits([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const id = ++requestIdRef.current;
    const timer = window.setTimeout(async () => {
      try {
        const results = await api.coach.search(q);
        if (id !== requestIdRef.current) return;
        setHits(results);
      } catch {
        if (id !== requestIdRef.current) return;
        setHits([]);
        push("Recherche impossible.", "error");
      } finally {
        if (id === requestIdRef.current) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, open, push]);

  if (!open) return null;

  function selectHit(hit: CoachSearchHit) {
    if (hit.message_id != null) {
      onJump(hit.message_id);
    } else {
      navigate("/profil");
    }
    onClose();
  }

  const trimmedQuery = query.trim();

  return (
    <div
      className="fixed inset-0 z-[1140]"
      role="dialog"
      aria-modal="true"
      aria-label="Rechercher dans le fil"
    >
      <button
        type="button"
        tabIndex={-1}
        onClick={onClose}
        aria-label="Fermer la recherche"
        className="absolute inset-0 h-full w-full animate-fade-in cursor-default
                   bg-sunken/60 backdrop-blur-sm"
      />
      <div
        className="absolute inset-x-0 bottom-0 z-[1150] flex max-h-[80vh] animate-sheet-up
                   flex-col rounded-t-2xl border-t border-border/[0.08] bg-card
                   shadow-card pb-[env(safe-area-inset-bottom)]"
      >
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border/[0.15]" aria-hidden="true" />
        <div className="flex items-center justify-between gap-2 px-4 pt-3">
          <h2 className="text-sm font-semibold tracking-tight">Rechercher dans le fil</h2>
          <div className="flex items-center gap-1">
            <Link
              to="/profil"
              onClick={onClose}
              className="btn-ghost"
              title="Mémoire du coach"
              aria-label="Mémoire du coach"
            >
              <Brain className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </Link>
            <button
              type="button"
              onClick={onClose}
              className="btn-ghost"
              title="Fermer"
              aria-label="Fermer"
            >
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="px-4 pt-3">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <input
              ref={inputRef}
              type="search"
              enterKeyHint="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Rechercher dans le fil…"
              className="input pl-9 pr-9"
              aria-label="Rechercher dans le fil"
            />
            {query && (
              <button
                type="button"
                onClick={() => {
                  setQuery("");
                  inputRef.current?.focus();
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-fg"
                aria-label="Effacer la recherche"
              >
                <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        <div className="mt-2 flex-1 overflow-y-auto px-2 pb-4">
          {!trimmedQuery ? (
            <p className="px-3 py-6 text-center text-sm text-muted">
              Tape un mot-clé pour retrouver un message, un résumé ou un fait de mémoire.
            </p>
          ) : hits.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted">
              {searching ? "Recherche…" : "Aucun résultat."}
            </p>
          ) : (
            hits.map((hit, i) => (
              <button
                key={`${hit.source_type}-${hit.message_id ?? "x"}-${i}`}
                type="button"
                onClick={() => selectHit(hit)}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-sunken"
              >
                <span
                  className={`mr-2 rounded px-1.5 py-0.5 text-[11px] font-medium ${
                    hit.message_id != null
                      ? "bg-accent/15 text-accent"
                      : "bg-overlay/10 text-muted"
                  }`}
                >
                  {SOURCE_LABELS[hit.source_type] || hit.source_type}
                </span>
                <span className="text-muted">{hit.text.slice(0, 140)}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
