import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { LoadPoint } from "../api/types";
import { CHART, axisProps, legendStyle, tooltipStyle } from "../chartTheme";

interface Props {
  data: LoadPoint[];
}

export default function LoadChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="card flex h-48 items-center justify-center text-muted text-sm">
        Pas de données sur la période.
      </div>
    );
  }
  return (
    <div className="card">
      <h3 className="label-eyebrow mb-2">Évolution charge — CTL / ATL / TSB</h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => (d as string).slice(5)}
              minTickGap={24}
              {...axisProps}
            />
            <YAxis {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} />
            <Legend wrapperStyle={legendStyle} />
            <Line
              type="monotone"
              dataKey="ctl"
              name="CTL"
              stroke={CHART.ctl}
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="atl"
              name="ATL"
              stroke={CHART.atl}
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="tsb"
              name="TSB"
              stroke={CHART.tsb}
              dot={false}
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
