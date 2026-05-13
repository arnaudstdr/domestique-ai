import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Workout } from "../api/types";
import {
  KIND_LABELS,
  KIND_TONES,
  PHASE_LABELS,
  formatDuration,
} from "./workoutKind";

interface Props {
  workouts: Workout[];
}

interface WeekGroup {
  weekStart: string;
  weekLabel: string;
  workouts: Workout[];
  totalTss: number;
  totalDuration: number;
}

function startOfWeek(dateStr: string): Date {
  const d = new Date(dateStr + "T00:00:00");
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

function formatWeekLabel(start: Date): string {
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const fmt = (d: Date) =>
    `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
  return `${fmt(start)} → ${fmt(end)}`;
}

function groupByWeek(workouts: Workout[]): WeekGroup[] {
  const map = new Map<string, WeekGroup>();
  for (const w of workouts) {
    const start = startOfWeek(w.date);
    const key = start.toISOString().slice(0, 10);
    let group = map.get(key);
    if (!group) {
      group = {
        weekStart: key,
        weekLabel: formatWeekLabel(start),
        workouts: [],
        totalTss: 0,
        totalDuration: 0,
      };
      map.set(key, group);
    }
    group.workouts.push(w);
    group.totalTss += w.estimated_tss;
    group.totalDuration += w.duration_min;
  }
  return Array.from(map.values()).sort((a, b) =>
    a.weekStart.localeCompare(b.weekStart),
  );
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function dayLabel(dateStr: string): string {
  return new Date(dateStr + "T00:00:00").toLocaleDateString("fr-FR", {
    weekday: "short",
  });
}

export default function PlanCalendar({ workouts }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);
  const weeks = groupByWeek(workouts);
  const chartData = weeks.map((w, i) => ({
    name: `S${i + 1}`,
    tss: Math.round(w.totalTss),
  }));

  if (workouts.length === 0) {
    return (
      <div className="card text-sm text-muted">Aucune séance dans ce plan.</div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="mb-2 text-xs font-medium text-gray-200">
          📊 TSS hebdomadaire
        </h3>
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              margin={{ top: 4, right: 8, left: -16, bottom: 0 }}
            >
              <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
              <XAxis dataKey="name" stroke="#9aa3af" fontSize={10} />
              <YAxis stroke="#9aa3af" fontSize={10} />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#23272f",
                  border: "1px solid #2c313a",
                  borderRadius: 8,
                  color: "#e5e7eb",
                  fontSize: 12,
                }}
              />
              <Bar dataKey="tss" fill="#f97316" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {weeks.map((week, weekIdx) => (
        <div key={week.weekStart} className="card space-y-2">
          <div className="flex items-center justify-between border-b border-white/5 pb-2">
            <div>
              <div className="text-sm font-medium text-gray-100">
                Semaine {weekIdx + 1}
              </div>
              <div className="text-xs text-muted">{week.weekLabel}</div>
            </div>
            <div className="text-right text-xs text-muted">
              <div>{Math.round(week.totalTss)} TSS</div>
              <div>{formatDuration(week.totalDuration * 60)}</div>
            </div>
          </div>

          {week.workouts.map((w) => {
            const wId = `${w.date}-${w.name}`;
            const isOpen = openId === wId;
            return (
              <div key={wId} className="rounded-lg bg-surface/40 p-2">
                <button
                  type="button"
                  onClick={() => setOpenId(isOpen ? null : wId)}
                  className="flex w-full items-center justify-between gap-2 text-left"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className={`pill ${KIND_TONES[w.kind] || "bg-muted/15 text-muted"}`}
                    >
                      {KIND_LABELS[w.kind] || w.kind}
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-sm text-gray-100">
                        {w.name}
                      </div>
                      <div className="text-xs text-muted">
                        {dayLabel(w.date)} {formatDate(w.date)} ·{" "}
                        {w.duration_min} min · {w.target_zone}
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
                    <span>{Math.round(w.estimated_tss)} TSS</span>
                    <span>{isOpen ? "▾" : "▸"}</span>
                  </div>
                </button>

                {isOpen && (
                  <div className="mt-2 space-y-1 border-t border-white/5 pt-2">
                    {w.notes && (
                      <div className="text-xs italic text-muted">{w.notes}</div>
                    )}
                    {w.structure.length === 0 ? (
                      <div className="text-xs text-muted">
                        Pas de structure détaillée.
                      </div>
                    ) : (
                      <ul className="space-y-1 text-xs">
                        {w.structure.map((s, idx) => (
                          <li
                            key={idx}
                            className="flex items-center justify-between rounded bg-surface/40 px-2 py-1"
                          >
                            <span className="text-gray-200">
                              {s.repeat > 1 && (
                                <span className="text-accent">
                                  ×{s.repeat}{" "}
                                </span>
                              )}
                              <span className="text-muted">
                                {PHASE_LABELS[s.phase]}
                              </span>{" "}
                              · {s.zone}
                            </span>
                            <span className="text-muted">
                              {formatDuration(s.duration_sec)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
