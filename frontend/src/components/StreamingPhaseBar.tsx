export type PhaseStatus = "idle" | "active" | "done";

export interface StreamPhases {
  thinking: PhaseStatus;
  tools: PhaseStatus;
  generating: PhaseStatus;
}

const PHASES = [
  { key: "thinking" as const, icon: "💭", label: "Réflexion" },
  { key: "tools" as const, icon: "🔧", label: "Outils" },
  { key: "generating" as const, icon: "✍️", label: "Réponse" },
];

export default function StreamingPhaseBar({ thinking, tools, generating }: StreamPhases) {
  const statuses = { thinking, tools, generating };
  return (
    <div className="flex items-center gap-1 mb-2 text-xs">
      {PHASES.map((phase, i) => {
        const status = statuses[phase.key];
        return (
          <div key={phase.key} className="flex items-center">
            {i > 0 && <span className="mx-1.5 text-muted/30">→</span>}
            <span
              className={`flex items-center gap-1 px-2 py-0.5 rounded-full border transition-all duration-300 ${
                status === "active"
                  ? "border-accent/50 text-accent bg-accent/10 animate-pulse"
                  : status === "done"
                  ? "border-emerald-500/20 text-emerald-400/50"
                  : "border-white/5 text-muted/20"
              }`}
            >
              {phase.icon} {phase.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
