import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, streamCoachChat } from "../api/client";
import type { CoachMessage, CoachSession } from "../api/types";
import ChatBubble from "../components/ChatBubble";
import { useToast } from "../hooks/useToast";

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

function TypingDots() {
  return (
    <div className="flex justify-start">
      <div className="bg-card border border-white/5 rounded-2xl px-4 py-3 flex items-center gap-1.5">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="w-2 h-2 rounded-full bg-gray-500 animate-bounce"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

export default function Coach() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<CoachSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<PendingAssistant>(EMPTY_PENDING);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingRef = useRef<PendingAssistant>(EMPTY_PENDING);
  // Flag levé quand on vient de recevoir un session_id du serveur (premier tour
  // d'une nouvelle session) : on doit alors ignorer le prochain fetch déclenché
  // par le changement de sessionId, sinon il écrase l'état local en cours de
  // streaming ou juste après.
  const skipNextFetchRef = useRef(false);

  useEffect(() => {
    api.coach.sessions().then(setSessions).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    if (skipNextFetchRef.current) {
      skipNextFetchRef.current = false;
      return;
    }
    let aborted = false;
    api.coach
      .messages(sessionId)
      .then((m) => {
        if (!aborted) setMessages(m);
      })
      .catch(() => undefined);
    return () => {
      aborted = true;
    };
  }, [sessionId]);

  useEffect(() => {
    const prefill = searchParams.get("prompt");
    if (prefill) {
      setDraft(prefill);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  // Auto-grow du textarea : on remet la hauteur à `auto` pour lire un
  // `scrollHeight` propre, puis on l'aligne dessus. Le `max-h-32` du CSS cap la
  // hauteur visible et `overflow-y-auto` prend le relais via le scroll interne.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  function updatePending(patch: (p: PendingAssistant) => PendingAssistant) {
    pendingRef.current = patch(pendingRef.current);
    setPending(pendingRef.current);
  }

  // Garde l'écran allumé pendant la génération : sur iOS, la veille de
  // l'écran ferme le fetch SSE et casse le streaming. Wake Lock est best-effort
  // (échoue silencieusement si non supporté ou refusé).
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

  async function send() {
    const message = draft.trim();
    if (!message || streaming) return;
    setDraft("");
    setStreaming(true);
    pendingRef.current = EMPTY_PENDING;
    setPending(EMPTY_PENDING);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: message, thinking: null, tool_calls: null },
    ]);
    await acquireWakeLock();

    try {
      await streamCoachChat({ session_id: sessionId, message }, (event) => {
        if (event.type === "session_id") {
          // Nouvelle session côté serveur : on évite que l'effect du
          // changement de sessionId déclenche un fetch qui écraserait le
          // streaming en cours.
          skipNextFetchRef.current = true;
          setSessionId(event.value);
        } else if (event.type === "thinking") {
          updatePending((p) => ({
            ...p,
            thinking: (p.thinking || "") + event.value,
          }));
        } else if (event.type === "token") {
          updatePending((p) => ({ ...p, content: p.content + event.value }));
        } else if (event.type === "tool_call") {
          updatePending((p) => ({
            ...p,
            toolCalls: [
              ...p.toolCalls,
              { name: event.name, arguments: event.args, result: null },
            ],
          }));
        } else if (event.type === "tool_result") {
          updatePending((p) => ({
            ...p,
            toolCalls: p.toolCalls.map((tc) =>
              tc.name === event.name && tc.result === null
                ? { ...tc, result: event.result }
                : tc,
            ),
          }));
        } else if (event.type === "error") {
          push(event.value, "error");
        }
      });
    } catch (err) {
      if (isNetworkError(err)) {
        push(
          "Connexion interrompue (écran verrouillé ou app en arrière-plan). On recharge la session — la réponse a peut-être abouti côté serveur.",
          "error",
        );
        // Le serveur a probablement persisté la réponse même si le stream est
        // coupé : on recharge la session pour récupérer l'assistant turn.
        if (sessionId) {
          try {
            const fresh = await api.coach.messages(sessionId);
            setMessages(fresh);
            pendingRef.current = EMPTY_PENDING;
            setPending(EMPTY_PENDING);
            return;
          } catch {
            // On retombe sur l'affichage du pending partiel ci-dessous.
          }
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
      api.coach.sessions().then(setSessions).catch(() => undefined);
    }
  }

  const { push } = useToast();

  function startNew() {
    setSessionId(null);
    setMessages([]);
    pendingRef.current = EMPTY_PENDING;
    setPending(EMPTY_PENDING);
  }

  async function deleteCurrentSession() {
    if (!sessionId) return;
    try {
      await api.coach.deleteSession(sessionId);
      startNew();
      api.coach.sessions().then(setSessions).catch(() => undefined);
    } catch {
      push("Impossible de supprimer la session.", "error");
    }
  }

  return (
    <div className="space-y-3">
      <div className="card">
        <div className="flex gap-2 items-center">
          <select
            value={sessionId || ""}
            onChange={(e) => setSessionId(e.target.value || null)}
            className="input flex-1"
          >
            <option value="">(nouvelle session)</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.started_at.slice(0, 16).replace("T", " ")} —{" "}
                {s.preview || "…"}
              </option>
            ))}
          </select>
          <button onClick={startNew} className="btn-ghost" title="Nouvelle session">
            🆕
          </button>
          <button
            onClick={deleteCurrentSession}
            disabled={!sessionId || streaming}
            className="btn-ghost disabled:opacity-40"
            title="Supprimer cette session"
          >
            🗑️
          </button>
        </div>
      </div>

      <div className="space-y-3 pb-40">
        {messages.map((m, i) => (
          <ChatBubble
            key={i}
            role={m.role}
            content={m.content}
            thinking={m.thinking}
            toolCalls={m.tool_calls}
          />
        ))}
        {streaming && !pending.content && <TypingDots />}
        {(streaming && pending.content) && (
          <ChatBubble
            role="assistant"
            content={pending.content}
            thinking={pending.thinking}
            toolCalls={pending.toolCalls}
          />
        )}
        <div ref={messagesEndRef} />
      </div>

      <div
        className="fixed bottom-16 inset-x-0 z-20 bg-surface/95 backdrop-blur
                   border-t border-white/5 pb-[env(safe-area-inset-bottom)]"
      >
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
            onClick={send}
            disabled={streaming || !draft.trim()}
            className="btn-primary"
          >
            {streaming ? "…" : "↑"}
          </button>
        </div>
      </div>
    </div>
  );
}
