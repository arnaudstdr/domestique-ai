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
      <h3 className="mb-2 text-sm font-medium text-gray-200">
        Évolution charge (CTL / ATL / TSB)
      </h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => (d as string).slice(5)}
              stroke="#9aa3af"
              fontSize={11}
              minTickGap={24}
            />
            <YAxis stroke="#9aa3af" fontSize={11} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#23272f",
                border: "1px solid #2c313a",
                borderRadius: 8,
                color: "#e5e7eb",
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "#9aa3af" }} />
            <Line
              type="monotone"
              dataKey="ctl"
              name="CTL"
              stroke="#3b82f6"
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="atl"
              name="ATL"
              stroke="#ef4444"
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="tsb"
              name="TSB"
              stroke="#22c55e"
              dot={false}
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
