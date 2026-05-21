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
      <h3 className="mb-2 text-sm font-medium text-gray-200">
        Charge longue durée (CTL / ATL / TSB)
      </h3>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(d) => formatTick(d as string, resolution)}
              stroke="#9aa3af"
              fontSize={11}
              minTickGap={28}
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
            <Line type="monotone" dataKey="ctl" name="CTL" stroke="#3b82f6" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="atl" name="ATL" stroke="#ef4444" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="tsb" name="TSB" stroke="#22c55e" dot={false} strokeWidth={2} />
            {showBrush && (
              <Brush
                dataKey="date"
                height={22}
                stroke="#3b82f6"
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
