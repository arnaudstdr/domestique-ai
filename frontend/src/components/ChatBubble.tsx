import { useState } from "react";

interface Props {
  role: "user" | "assistant";
  content: string;
  thinking?: string | null;
  toolCalls?: { name: string; arguments: unknown; result: unknown }[] | null;
}

export default function ChatBubble({ role, content, thinking, toolCalls }: Props) {
  const [openThinking, setOpenThinking] = useState(false);
  const [openTools, setOpenTools] = useState(false);
  const isUser = role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap leading-relaxed ${
          isUser ? "bg-accent text-white" : "bg-card border border-white/5 text-gray-100"
        }`}
      >
        {content || (
          <span className="italic text-muted">…</span>
        )}
        {!isUser && thinking && (
          <details
            open={openThinking}
            onToggle={(e) => setOpenThinking((e.target as HTMLDetailsElement).open)}
            className="mt-3 border-t border-white/5 pt-2"
          >
            <summary className="cursor-pointer text-xs text-muted">
              💡 Raisonnement
            </summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-muted">
              {thinking}
            </pre>
          </details>
        )}
        {!isUser && toolCalls && toolCalls.length > 0 && (
          <details
            open={openTools}
            onToggle={(e) => setOpenTools((e.target as HTMLDetailsElement).open)}
            className="mt-2 border-t border-white/5 pt-2"
          >
            <summary className="cursor-pointer text-xs text-muted">
              🔧 Outils appelés ({toolCalls.length})
            </summary>
            <div className="mt-2 space-y-2">
              {toolCalls.map((tc, i) => (
                <div key={i} className="rounded-lg bg-black/30 p-2">
                  <div className="text-xs font-mono text-accent">{tc.name}</div>
                  <pre className="mt-1 text-[11px] text-muted overflow-x-auto">
                    {JSON.stringify(tc.result, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
