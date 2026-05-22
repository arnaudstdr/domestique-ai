// Mapping partagé entre PlanCalendar et DailyBriefCard pour les types de séance.

import type { WorkoutStep } from "../api/types";

export const KIND_LABELS: Record<string, string> = {
  recovery: "Récup",
  endurance: "Endurance",
  tempo: "Tempo",
  intervals: "Intervalles",
};

export const KIND_TONES: Record<string, string> = {
  recovery: "bg-green-500/15 text-green-400",
  endurance: "bg-blue-500/15 text-blue-300",
  tempo: "bg-orange-500/15 text-orange-300",
  intervals: "bg-red-500/15 text-red-400",
};

export const PHASE_LABELS: Record<WorkoutStep["phase"], string> = {
  warmup: "Échauffement",
  active: "Effort",
  rest: "Récup",
  cooldown: "Retour calme",
};

export function formatDuration(sec: number): string {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest === 0 ? `${h} h` : `${h} h ${rest}`;
}
