import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WeeklyVolumeEntry } from "../api/types";
import { CHART, axisProps, tooltipStyle } from "../chartTheme";

interface Props {
  data: WeeklyVolumeEntry[];
}

const MONTH_FR = [
  "Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
  "Juil", "Août", "Sep", "Oct", "Nov", "Déc",
];

function formatDay(iso: string): string {
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

function formatMonth(iso: string): string {
  const idx = Number(iso.slice(5, 7)) - 1;
  return MONTH_FR[idx] ?? iso.slice(5, 7);
}

export default function WeeklyVolumeChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="card flex h-56 items-center justify-center text-muted text-sm">
        Pas de volumes à afficher.
      </div>
    );
  }
  const currentWeek = data[data.length - 1]?.week_starting;
  return (
    <div className="card">
      <h3 className="label-eyebrow mb-2">
        Km vélo par semaine — {data.length} dernières semaines
      </h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id="weeklyVolumeGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART.accent} stopOpacity={0.35} />
                <stop offset="100%" stopColor={CHART.accent} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis
              dataKey="week_starting"
              tickFormatter={(d) => formatMonth(d as string)}
              minTickGap={16}
              {...axisProps}
            />
            <YAxis
              tickFormatter={(v) => `${v} km`}
              width={52}
              {...axisProps}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value: number) => [
                `${value.toLocaleString("fr-FR")} km`,
                "Distance",
              ]}
              labelFormatter={(d) => `Semaine du ${formatDay(d as string)}`}
            />
            {currentWeek && (
              <ReferenceLine x={currentWeek} stroke="rgba(255,255,255,0.35)" />
            )}
            <Area
              type="monotone"
              dataKey="distance_km"
              name="Distance"
              stroke={CHART.accent}
              fill="url(#weeklyVolumeGrad)"
              strokeWidth={2}
              dot={{ r: 3, fill: CHART.accent, strokeWidth: 0 }}
              activeDot={{ r: 5 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
