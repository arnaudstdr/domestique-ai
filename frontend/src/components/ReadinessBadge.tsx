export type ReadinessTone = "danger" | "warning" | "good" | "accent";

export interface ReadinessMeta {
  label: string;
  tone: ReadinessTone;
}

/** Bande de readiness à partir du score /100 (bandes alignées sur le backend). */
export function readinessMeta(score: number): ReadinessMeta {
  if (score >= 85) return { label: "Pic", tone: "accent" };
  if (score >= 70) return { label: "Élevé", tone: "good" };
  if (score >= 50) return { label: "Équilibré", tone: "good" };
  if (score >= 30) return { label: "Faible", tone: "warning" };
  return { label: "Très faible", tone: "danger" };
}

const TONE_CLASS: Record<ReadinessTone, string> = {
  danger: "bg-red-500/10 text-red-500",
  warning: "bg-amber-500/10 text-amber-500",
  accent: "bg-accent/10 text-accent",
  good: "bg-emerald-500/10 text-emerald-500",
};

export default function ReadinessBadge({ score }: { score: number }) {
  const { label, tone } = readinessMeta(score);
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${TONE_CLASS[tone]}`}
    >
      {label}
    </span>
  );
}
