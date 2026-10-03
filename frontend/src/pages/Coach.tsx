import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import { api, ApiError, streamCoachChat } from "../api/client";
import type { CoachMessage } from "../api/types";
import ChatBubble from "../components/ChatBubble";
import CoachSearchSheet from "../components/CoachSearchSheet";
import LlmDisclaimer from "../components/LlmDisclaimer";
import { useToast } from "../hooks/useToast";

const PAGE_SIZE = 30;

const SUGGESTED_PROMPTS = [
  "Comment j'évolue sur les 6 derniers mois ?",
  "Où va mon FTP ?",
  "C'est quoi ma Z2 en bpm ?",
  "Mes sorties longues progressent-elles ?",
  "Combien de séances de seuil ce trimestre ?",
  "Qu'est-ce que je fais aujourd'hui ?",
];

interface PendingAssistant {
  content: string;
  thinking: string | null;
  toolCalls: { name: string; arguments: unknown; result: unknown }[];
}

const EMPTY_PENDING: PendingAssistant = {
  content: "",
  thinking: null,
  toolCalls: [],
};

interface ThreadMessage extends CoachMessage {
  key: string;
}

let localSeq = 0;

function localKey(): string {
  localSeq += 1;
  return `local-${localSeq}-${Date.now()}`;
}

function toThread(message: CoachMessage): ThreadMessage {
  return { ...message, key: message.id != null ? `id-${message.id}` : localKey() };
}

function TypingDots() {
  return (
    <div className="flex justify-start">
      <div className="bg-card border border-border/[0.07] shadow-card rounded-[18px_18px_18px_4px] px-4 py-3 flex items-center gap-1.5">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="w-2 h-2 rounded-full bg-accent/70 animate-bounce"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

export default function Coach() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [hasMoreBefore, setHasMoreBefore] = useState(false);
  const [hasMoreAfter, setHasMoreAfter] = useState(false);
  const [loadingBefore, setLoadingBefore] = useState(false);
  const [loadingAfter, setLoadingAfter] = useState(false);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<PendingAssistant>(EMPTY_PENDING);
  const [searchOpen, setSearchOpen] = useState(false);
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const pendingRef = useRef<PendingAssistant>(EMPTY_PENDING);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const topSentinelRef = useRef<HTMLDivElement | null>(null);
  const bottomSentinelRef = useRef<HTMLDivElement | null>(null);
  // Hauteur de scroll mémorisée avant un prepend, pour rester visuellement au
  // même endroit quand on remonte le fil.
  const prevScrollHeightRef = useRef<number | null>(null);

  function updatePending(patch: (p: PendingAssistant) => PendingAssistant) {
    pendingRef.current = patch(pendingRef.current);
    setPending(pendingRef.current);
  }

  function scrollToBottom(behavior: ScrollBehavior = "smooth") {
    bottomRef.current?.scrollIntoView({ behavior });
  }

  function isNearBottom(): boolean {
    const el = document.documentElement;
    return el.scrollHeight - el.scrollTop - window.innerHeight < 160;
  }

  async function loadThreadLatest() {
    const page = await api.coach.thread({ limit: PAGE_SIZE });
    setMessages(page.messages.map(toThread));
    setHasMoreBefore(page.has_more_before);
    setHasMoreAfter(page.has_more_after);
  }

  async function loadBefore() {
    if (loadingBefore || !hasMoreBefore) return;
    const oldest = messages[0]?.id;
    if (oldest == null) return;
    setLoadingBefore(true);
    prevScrollHeightRef.current = document.documentElement.scrollHeight;
    try {
      const page = await api.coach.thread({ before: oldest, limit: PAGE_SIZE });
      setMessages((prev) => [...page.messages.map(toThread), ...prev]);
      setHasMoreBefore(page.has_more_before);
    } catch {
      prevScrollHeightRef.current = null;
    } finally {
      setLoadingBefore(false);
    }
  }

  async function loadAfter() {
    if (loadingAfter || !hasMoreAfter) return;
    const newest = messages[messages.length - 1]?.id;
    if (newest == null) return;
    setLoadingAfter(true);
    try {
      const page = await api.coach.thread({ after: newest, limit: PAGE_SIZE });
      setMessages((prev) => [...prev, ...page.messages.map(toThread)]);
      setHasMoreAfter(page.has_more_after);
    } catch {
      // silencieux : on retentera au prochain scroll
    } finally {
      setLoadingAfter(false);
    }
  }

  const loadBeforeRef = useRef(loadBefore);
  const loadAfterRef = useRef(loadAfter);
  useEffect(() => {
    loadBeforeRef.current = loadBefore;
    loadAfterRef.current = loadAfter;
  });

  useEffect(() => {
    let aborted = false;
    api.coach
      .thread({ limit: PAGE_SIZE })
      .then((page) => {
        if (aborted) return;
        setMessages(page.messages.map(toThread));
        setHasMoreBefore(page.has_more_before);
        setHasMoreAfter(page.has_more_after);
        requestAnimationFrame(() => scrollToBottom("auto"));
      })
      .catch(() => undefined);
    return () => {
      aborted = true;
    };
  }, []);

  useEffect(() => {
    const top = topSentinelRef.current;
    const bottom = bottomSentinelRef.current;
    if (!top || !bottom) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          if (entry.target === top) loadBeforeRef.current();
          else loadAfterRef.current();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(top);
    observer.observe(bottom);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const prev = prevScrollHeightRef.current;
    if (prev == null) return;
    prevScrollHeightRef.current = null;
    const delta = document.documentElement.scrollHeight - prev;
    if (delta > 0) window.scrollBy(0, delta);
  }, [messages]);

  useEffect(() => {
    if (isNearBottom()) scrollToBottom("smooth");
  }, [messages, pending]);

  useEffect(() => {
    const prefill = searchParams.get("prompt");
    if (prefill) {
      setDraft(prefill);
      setSearchParams({}, { replace: true });
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  useEffect(() => {
    if (highlightId == null) return;
    const timer = window.setTimeout(() => setHighlightId(null), 4000);
    return () => window.clearTimeout(timer);
  }, [highlightId]);

  // Garde l'écran allumé pendant la génération (iOS coupe le SSE en veille).
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);

  async function acquireWakeLock(): Promise<void> {
    try {
      if ("wakeLock" in navigator) {
        wakeLockRef.current = await navigator.wakeLock.request("screen");
      }
    } catch {
      // Pas critique, on laisse le système gérer la veille.
    }
  }

  function releaseWakeLock(): void {
    wakeLockRef.current?.release().catch(() => undefined);
    wakeLockRef.current = null;
  }

  function isNetworkError(err: unknown): boolean {
    const msg = String(err).toLowerCase();
    return (
      msg.includes("load failed") ||
      msg.includes("failed to fetch") ||
      msg.includes("network") ||
      msg.includes("aborted")
    );
  }

  async function send(override?: string) {
    const message = (override ?? draft).trim();
    if (!message || streaming) return;
    setDraft("");
    setStreaming(true);
    setHighlightId(null);
    pendingRef.current = EMPTY_PENDING;
    setPending(EMPTY_PENDING);
    setMessages((prev) => [
      ...prev,
      { key: localKey(), role: "user", content: message, thinking: null, tool_calls: null },
    ]);
    await acquireWakeLock();

    try {
      await streamCoachChat({ session_id: null, message }, (event) => {
        if (event.type === "thinking") {
          updatePending((p) => ({ ...p, thinking: (p.thinking || "") + event.value }));
        } else if (event.type === "token") {
          updatePending((p) => ({ ...p, content: p.content + event.value }));
        } else if (event.type === "tool_call") {
          updatePending((p) => ({
            ...p,
            toolCalls: [...p.toolCalls, { name: event.name, arguments: event.args, result: null }],
          }));
        } else if (event.type === "tool_result") {
          updatePending((p) => ({
            ...p,
            toolCalls: p.toolCalls.map((tc) =>
              tc.name === event.name && tc.result === null ? { ...tc, result: event.result } : tc,
            ),
          }));
        } else if (event.type === "error") {
          push(event.value, "error");
        }
      });
    } catch (err) {
      if (isNetworkError(err)) {
        push(
          "Connexion interrompue (écran verrouillé ou app en arrière-plan). On recharge le fil — la réponse a peut-être abouti côté serveur.",
          "error",
        );
        try {
          await loadThreadLatest();
          pendingRef.current = EMPTY_PENDING;
          setPending(EMPTY_PENDING);
          return;
        } catch {
          // On retombe sur l'affichage du pending partiel ci-dessous.
        }
      } else {
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Coach : ${msg}`, "error");
      }
    } finally {
      releaseWakeLock();
      const final = pendingRef.current;
      if (final.content || final.thinking || final.toolCalls.length > 0) {
        setMessages((prev) => [
          ...prev,
          {
            key: localKey(),
            role: "assistant",
            content: final.content,
            thinking: final.thinking,
            tool_calls: final.toolCalls,
          },
        ]);
      }
      pendingRef.current = EMPTY_PENDING;
      setPending(EMPTY_PENDING);
      setStreaming(false);
    }
  }

  async function jumpTo(messageId: number) {
    if (streaming) return;
    try {
      const page = await api.coach.thread({ anchor: messageId, limit: PAGE_SIZE });
      setMessages(page.messages.map(toThread));
      setHasMoreBefore(page.has_more_before);
      setHasMoreAfter(page.has_more_after);
      setHighlightId(messageId);
      requestAnimationFrame(() => {
        document
          .getElementById(`msg-${messageId}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    } catch {
      push("Impossible de charger ce message.", "error");
    }
  }

  const { push } = useToast();

  return (
    <div className="space-y-3">
      <div className="space-y-3 pb-48">
        <div ref={topSentinelRef} className="h-1" />
        {loadingBefore && <div className="py-2 text-center text-xs text-muted">Chargement…</div>}
        {!hasMoreBefore && messages.length > 0 && (
          <div className="py-4 text-center text-xs text-muted">Début de la conversation</div>
        )}
        {messages.length === 0 && !streaming && (
          <div className="px-4 pt-8">
            <p className="mb-3 text-center text-sm text-muted">Une question pour démarrer ?</p>
            <div className="mx-auto flex max-w-2xl flex-wrap justify-center gap-2">
              {SUGGESTED_PROMPTS.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => void send(question)}
                  className="rounded-full border border-border/[0.12] bg-card px-3 py-1.5
                             text-sm text-fg-soft shadow-card transition-colors duration-150
                             hover:border-accent/50 hover:text-fg active:translate-y-px"
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.key}
            id={m.id != null ? `msg-${m.id}` : undefined}
            className={
              m.id != null && m.id === highlightId
                ? "rounded-2xl ring-2 ring-accent transition"
                : undefined
            }
          >
            <ChatBubble
              role={m.role}
              content={m.content}
              thinking={m.thinking}
              toolCalls={m.tool_calls}
            />
          </div>
        ))}
        {streaming && !pending.content && <TypingDots />}
        {streaming && pending.content && (
          <ChatBubble
            role="assistant"
            content={pending.content}
            thinking={pending.thinking}
            toolCalls={pending.toolCalls}
          />
        )}
        {loadingAfter && <div className="py-2 text-center text-xs text-muted">Chargement…</div>}
        <div ref={bottomSentinelRef} className="h-1" />
        <div ref={bottomRef} />
      </div>

      <div
        className="fixed bottom-16 inset-x-0 z-20 bg-surface/80 backdrop-blur-xl
                   border-t border-border/[0.06] pb-[env(safe-area-inset-bottom)]"
      >
        <div className="pointer-events-none absolute -top-14 inset-x-0 mx-auto flex max-w-3xl justify-end px-4">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="pointer-events-auto flex h-12 w-12 items-center justify-center rounded-full
                       bg-accent text-accent-ink shadow-glow transition-all duration-150
                       hover:-translate-y-px active:translate-y-0"
            title="Rechercher dans le fil"
            aria-label="Rechercher dans le fil"
            aria-haspopup="dialog"
            aria-expanded={searchOpen}
          >
            <Search className="h-5 w-5" strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
        <LlmDisclaimer className="mx-auto max-w-3xl px-4 pt-3" />
        <div className="mx-auto max-w-3xl px-4 py-3 flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder="Pose une question au coach…"
            className="input resize-none max-h-32 overflow-y-auto"
          />
          <button
            onClick={() => void send()}
            disabled={streaming || !draft.trim()}
            className="btn-primary"
          >
            {streaming ? "…" : "↑"}
          </button>
        </div>
      </div>

      <CoachSearchSheet open={searchOpen} onClose={() => setSearchOpen(false)} onJump={jumpTo} />
    </div>
  );
}
