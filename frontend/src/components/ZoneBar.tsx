import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Props {
  zones: Record<string, number>; // z1..z5 en secondes
}

const ZONE_COLORS: Record<string, string> = {
  z1: "#3b82f6",
  z2: "#22c55e",
  z3: "#facc15",
  z4: "#f97316",
  z5: "#ef4444",
};

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
    fill: ZONE_COLORS[key] || "#9aa3af",
  }));
  const total = Object.values(zones).reduce((a, b) => a + b, 0);
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium text-gray-200">Répartition zones HR</h3>
        <span className="text-xs text-muted">{formatMin(total)} total</span>
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          >
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" stroke="#9aa3af" fontSize={11} />
            <YAxis dataKey="zone" type="category" stroke="#9aa3af" fontSize={11} width={36} />
            <Tooltip
              cursor={{ fill: "#2c313a" }}
              contentStyle={{
                backgroundColor: "#23272f",
                border: "1px solid #2c313a",
                borderRadius: 8,
                color: "#e5e7eb",
              }}
              formatter={(v) => [`${v} min`, "Temps"]}
            />
            <Bar dataKey="minutes" radius={[0, 6, 6, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
