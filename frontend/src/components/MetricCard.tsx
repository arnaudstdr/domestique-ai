interface Props {
  label: string;
  value: string;
  hint?: string;
  badge?: { label: string; tone?: "accent" | "good" | "warn" | "danger" };
}

const BADGE_TONES: Record<string, string> = {
  accent: "bg-accent/15 text-accent",
  good: "bg-green-500/15 text-green-400",
  warn: "bg-orange-500/15 text-orange-300",
  danger: "bg-red-500/15 text-red-400",
};

export default function MetricCard({ label, value, hint, badge }: Props) {
  return (
    <div className="card flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-muted">
          {label}
        </span>
        {badge && (
          <span
            className={`pill ${BADGE_TONES[badge.tone || "accent"] || BADGE_TONES.accent}`}
          >
            {badge.label}
          </span>
        )}
      </div>
      <span className="text-2xl font-semibold text-gray-100">{value}</span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}
