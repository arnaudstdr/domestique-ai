import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART, ZONE_COLORS, axisProps, tooltipStyle } from "../chartTheme";

interface Props {
  zones: Record<string, number>; // z1..z5 en secondes
}

function formatMin(sec: number): string {
  const m = Math.round(sec / 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${(m % 60).toString().padStart(2, "0")}`;
  }
  return `${m} min`;
}

export default function ZoneBar({ zones }: Props) {
  const data = Object.entries(zones).map(([key, sec]) => ({
    zone: key.toUpperCase(),
    minutes: Math.round(sec / 60),
    fill: ZONE_COLORS[key] || CHART.muted,
  }));
  const total = Object.values(zones).reduce((a, b) => a + b, 0);
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h3 className="label-eyebrow">Répartition zones HR</h3>
        <span className="metric-num text-xs text-muted">{formatMin(total)} total</span>
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          >
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" {...axisProps} />
            <YAxis dataKey="zone" type="category" width={36} {...axisProps} />
            <Tooltip
              cursor={{ fill: "rgba(255,255,255,0.04)" }}
              contentStyle={tooltipStyle}
              formatter={(v) => [`${v} min`, "Temps"]}
            />
            <Bar dataKey="minutes" radius={[0, 6, 6, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
