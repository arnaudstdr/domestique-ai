import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendMonthlyEntry } from "../api/types";
import {
  CHART,
  ZONE_COLORS,
  axisProps,
  legendStyle,
  tooltipStyle,
} from "../chartTheme";

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

export default function ZoneDistributionChart({ data }: Props) {
  // On ne garde que les mois où la ventilation a effectivement été calculée
  // (z1_pct non null). Affiche un état vide explicite si la base manque encore
  // de backfill.
  const filtered = data.filter((d) => d.z1_pct != null);
  if (filtered.length === 0) {
    return (
      <div className="card flex h-56 items-center justify-center text-muted text-sm text-center px-4">
        Zones HR non encore ventilées sur la période. Lance « Backfill HR »
        depuis le Dashboard pour les calculer.
      </div>
    );
  }
  return (
    <div className="card">
      <h3 className="label-eyebrow mb-2">
        Distribution mensuelle des zones HR (%)
      </h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={filtered}
            margin={{ top: 8, right: 8, left: -16, bottom: 0 }}
            stackOffset="expand"
          >
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis
              dataKey="month"
              tickFormatter={(m) => formatMonth(m as string)}
              minTickGap={16}
              {...axisProps}
            />
            <YAxis
              {...axisProps}
              tickFormatter={(v) => `${Math.round((v as number) * 100)}%`}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value: number, name: string) => [
                `${value.toFixed(1)} %`,
                name.toUpperCase(),
              ]}
              labelFormatter={(m) => formatMonth(m as string)}
            />
            <Legend wrapperStyle={legendStyle} />
            <Bar dataKey="z1_pct" name="Z1" stackId="z" fill={ZONE_COLORS.z1} />
            <Bar dataKey="z2_pct" name="Z2" stackId="z" fill={ZONE_COLORS.z2} />
            <Bar dataKey="z3_pct" name="Z3" stackId="z" fill={ZONE_COLORS.z3} />
            <Bar dataKey="z4_pct" name="Z4" stackId="z" fill={ZONE_COLORS.z4} />
            <Bar dataKey="z5_pct" name="Z5" stackId="z" fill={ZONE_COLORS.z5} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
