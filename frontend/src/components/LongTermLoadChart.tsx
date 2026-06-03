import {
  Brush,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendLoadPoint, TrendResolution } from "../api/types";
import { CHART, axisProps, legendStyle, tooltipStyle } from "../chartTheme";

interface Props {
  data: TrendLoadPoint[];
  resolution: TrendResolution;
}

/** Formate l'étiquette d'axe X selon la résolution choisie côté serveur. */
function formatTick(date: string, resolution: TrendResolution): string {
  if (resolution === "month") return date.slice(0, 7);
  if (resolution === "week") return date.slice(5);
  return date.slice(5);
}

export default function LongTermLoadChart({ data, resolution }: Props) {
  if (data.length === 0) {
    return (
      <div className="card flex h-56 items-center justify-center text-muted text-sm">
        Pas de données sur la période.
      </div>
    );
  }
  // Brush activé seulement quand la série est assez longue pour avoir un sens.
  const showBrush = data.length >= 30;
  return (
    <div className="card">
      <h3 className="label-eyebrow mb-2">Charge longue durée — CTL / ATL / TSB</h3>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => formatTick(d as string, resolution)}
              minTickGap={28}
              {...axisProps}
            />
            <YAxis {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} />
            <Legend wrapperStyle={legendStyle} />
            <Line type="monotone" dataKey="ctl" name="CTL" stroke={CHART.ctl} dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="atl" name="ATL" stroke={CHART.atl} dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="tsb" name="TSB" stroke={CHART.tsb} dot={false} strokeWidth={2} />
            {showBrush && (
              <Brush
                dataKey="date"
                height={22}
                stroke={CHART.ctl}
                fill="rgba(255,255,255,0.02)"
                travellerWidth={8}
                tickFormatter={(d) => formatTick(d as string, resolution)}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
