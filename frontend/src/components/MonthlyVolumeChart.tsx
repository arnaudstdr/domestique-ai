import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendMonthlyEntry } from "../api/types";

interface Props {
  data: TrendMonthlyEntry[];
}

const MONTH_FR = [
  "Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
  "Juil", "Août", "Sep", "Oct", "Nov", "Déc",
];

function formatMonth(month: string): string {
  const idx = Number(month.slice(5, 7)) - 1;
  return MONTH_FR[idx] ?? month.slice(5);
}

export default function MonthlyVolumeChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="card flex h-56 items-center justify-center text-muted text-sm">
        Pas de volumes à afficher.
      </div>
    );
  }
  // On vérifie s'il existe au moins un mois avec une donnée N-1 ; sinon on
  // masque la ligne (et la légende correspondante) pour ne pas afficher
  // une ligne plate à zéro qui ferait croire à un effondrement.
  const hasN1 = data.some((d) => d.distance_km_n1 != null);
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-medium text-gray-200">
        Volumes mensuels (km){hasN1 && " — vs N-1"}
      </h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
            <XAxis
              dataKey="month"
              tickFormatter={(m) => formatMonth(m as string)}
              stroke="#9aa3af"
              fontSize={11}
              minTickGap={16}
            />
            <YAxis stroke="#9aa3af" fontSize={11} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#23272f",
                border: "1px solid #2c313a",
                borderRadius: 8,
                color: "#e5e7eb",
              }}
              formatter={(value: number, name: string) => [
                `${value.toLocaleString("fr-FR")} km`,
                name,
              ]}
              labelFormatter={(m) => formatMonth(m as string)}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "#9aa3af" }} />
            <Bar dataKey="distance_km" name="Cette année" fill="#f97316" radius={[4, 4, 0, 0]} />
            {hasN1 && (
              <Line
                type="monotone"
                dataKey="distance_km_n1"
                name="N-1"
                stroke="#9aa3af"
                strokeDasharray="4 4"
                dot={{ r: 3 }}
                strokeWidth={2}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
