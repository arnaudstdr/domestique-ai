import { Link } from "react-router-dom";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import type { SimilarActivitiesResponse } from "../api/types";

interface Props {
  data: SimilarActivitiesResponse;
}

function formatHm(seconds: number | null): string {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h${m.toString().padStart(2, "0")}` : `${m} min`;
}

function formatDeltaPct(pct: number | null): JSX.Element {
  if (pct == null) {
    return <span className="text-muted">—</span>;
  }
  const arrow = pct > 0 ? "↑" : pct < 0 ? "↓" : "→";
  // Pour le TSS et la puissance : positif = mieux/plus dur. Pour la durée :
  // positif = plus long. On affiche neutre pour l'instant — l'utilisateur
  // interprète le sens (montée intensité ou ralentissement).
  const tone =
    Math.abs(pct) < 1
      ? "text-muted"
      : pct > 0
        ? "text-emerald-300"
        : "text-orange-300";
  return (
    <span className={tone}>
      {arrow} {Math.abs(pct).toFixed(1)} %
    </span>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function SimilarActivities({ data }: Props) {
  if (!data.available) {
    return (
      <div className="card text-sm text-muted">
        {data.reason || "Comparaison indisponible."}
      </div>
    );
  }
  if (data.matches.length === 0) {
    return (
      <div className="card text-sm text-muted">
        Aucune activité similaire dans ton historique (tolérance ±
        {data.criteria?.distance_tolerance_pct.toFixed(0) ?? 5} % sur la
        distance, ±{data.criteria?.elevation_tolerance_pct.toFixed(0) ?? 10} %
        sur le dénivelé).
      </div>
    );
  }

  // Sparkline TSS : on inclut la référence, puis les matches par ordre
  // chronologique croissant pour que la ligne aille de gauche (passé) à
  // droite (récent).
  const sparkData = [
    {
      label: "réf",
      tss: data.reference?.training_load ?? 0,
    },
    ...[...data.matches]
      .reverse()
      .map((m) => ({
        label: m.date.slice(0, 10),
        tss: m.training_load ?? 0,
      })),
  ];
  const hasSparkData = sparkData.some((p) => p.tss > 0);

  return (
    <div className="card space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-gray-200">
          Activités similaires
        </h3>
        <span className="pill bg-accent/10 text-accent">
          {data.matches.length}
        </span>
      </div>
      <p className="text-xs text-muted">
        Même profil ({data.reference?.distance_km.toFixed(0)} km ·{" "}
        {Math.round(data.reference?.elevation_m ?? 0)} m D+) en{" "}
        {data.reference?.sport_bucket === "indoor"
          ? "home trainer"
          : data.reference?.sport_bucket === "outdoor"
            ? "extérieur"
            : "autre"}
        .
      </p>

      {hasSparkData && (
        <div className="h-16">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
              <Tooltip
                contentStyle={{
                  backgroundColor: "#23272f",
                  border: "1px solid #2c313a",
                  borderRadius: 8,
                  color: "#e5e7eb",
                  fontSize: 11,
                }}
                formatter={(value: number) => [`${Math.round(value)} TSS`, "Charge"]}
                labelFormatter={(label) => String(label)}
              />
              <Line
                type="monotone"
                dataKey="tss"
                stroke="#f97316"
                strokeWidth={2}
                dot={{ r: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted text-left">
              <th className="py-1 font-normal">Date</th>
              <th className="py-1 font-normal">Durée</th>
              <th className="py-1 font-normal">TSS</th>
              <th className="py-1 font-normal">ΔTSS</th>
              <th className="py-1 font-normal">ΔPwr</th>
            </tr>
          </thead>
          <tbody>
            {data.matches.map((m) => (
              <tr
                key={m.strava_id}
                className="border-t border-white/5"
              >
                <td className="py-1.5">
                  <Link
                    to={`/activites/${m.strava_id}`}
                    className="text-accent hover:underline"
                  >
                    {formatDate(m.date)}
                  </Link>
                </td>
                <td className="py-1.5 text-gray-200">{formatHm(m.duration_sec)}</td>
                <td className="py-1.5 text-gray-200">
                  {m.training_load != null ? Math.round(m.training_load) : "—"}
                </td>
                <td className="py-1.5">{formatDeltaPct(m.tss_delta_pct)}</td>
                <td className="py-1.5">{formatDeltaPct(m.power_delta_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
