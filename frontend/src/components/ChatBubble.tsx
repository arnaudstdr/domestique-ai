import { useState } from "react";
import { Lightbulb, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "bg-accent text-white whitespace-pre-wrap"
            : "bg-card border border-white/5 text-gray-100"
        }`}
      >
        {isUser ? (
          content || <span className="italic text-muted">…</span>
        ) : content ? (
          <MarkdownBody content={content} />
        ) : (
          <span className="italic text-muted">…</span>
        )}
        {!isUser && thinking && (
          <details
            open={openThinking}
            onToggle={(e) => setOpenThinking((e.target as HTMLDetailsElement).open)}
            className="mt-3 border-t border-white/5 pt-2"
          >
            <summary className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
              <Lightbulb className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Raisonnement
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
            <summary className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
              <Wrench className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Outils appelés ({toolCalls.length})
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

function MarkdownBody({ content }: { content: string }) {
  return (
    <div
      className="prose prose-invert prose-sm max-w-none
                 prose-p:my-2 prose-p:leading-relaxed
                 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5
                 prose-headings:my-2 prose-headings:text-gray-100
                 prose-strong:text-gray-50
                 prose-code:bg-black/30 prose-code:px-1 prose-code:py-0.5
                 prose-code:rounded prose-code:text-accent prose-code:before:content-none
                 prose-code:after:content-none
                 prose-a:text-accent prose-a:no-underline hover:prose-a:underline"
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
