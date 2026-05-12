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

export default function Coach() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<CoachSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<PendingAssistant>(EMPTY_PENDING);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
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

  function updatePending(patch: (p: PendingAssistant) => PendingAssistant) {
    pendingRef.current = patch(pendingRef.current);
    setPending(pendingRef.current);
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

    try {
      await streamCoachChat({ session_id: sessionId, message }, (event) => {
        if (event.type === "session_id") {
          // Nouvelle session côté serveur : on évite que l'effect du
          // changement de sessionId déclenche un fetch qui écraserait le
          // streaming en cours.
          skipNextFetchRef.current = true;
          setSessionId(event.value);
        } else if (event.type === "thinking") {
          updatePending((p) => ({ ...p, thinking: event.value }));
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
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Coach : ${msg}`, "error");
    } finally {
      const final = pendingRef.current;
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: final.content,
          thinking: final.thinking,
          tool_calls: final.toolCalls,
        },
      ]);
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
          <button onClick={startNew} className="btn-ghost">
            🆕
          </button>
        </div>
      </div>

      <div className="space-y-3 pb-4">
        {messages.map((m, i) => (
          <ChatBubble
            key={i}
            role={m.role}
            content={m.content}
            thinking={m.thinking}
            toolCalls={m.tool_calls}
          />
        ))}
        {(streaming || pending.content || pending.thinking) && (
          <ChatBubble
            role="assistant"
            content={pending.content || "…"}
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
            className="input resize-none max-h-32"
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
