import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, ApiError } from "../api/client";
import type { ActivityDetail as ActivityDetailType } from "../api/types";
import ActivityMap from "../components/ActivityMap";
import MetricCard from "../components/MetricCard";
import ZoneBar from "../components/ZoneBar";
import { useToast } from "../hooks/useToast";

function formatHms(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m.toString().padStart(2, "0")}` : `${m} min`;
}

function buildSeries(
  time: number[] | null,
  values: number[] | null,
  key: string,
) {
  if (!time || !values) return [];
  const n = Math.min(time.length, values.length);
  const out: Record<string, number>[] = [];
  for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 600))) {
    out.push({ t: Math.round(time[i] / 60), [key]: values[i] });
  }
  return out;
}

export default function ActivityDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ActivityDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const { push } = useToast();

  useEffect(() => {
    let aborted = false;
    setLoading(true);
    api.activities
      .detail(Number(id))
      .then((d) => {
        if (!aborted) setDetail(d);
      })
      .catch((err) => {
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Erreur : ${msg}`, "error");
      })
      .finally(() => {
        if (!aborted) setLoading(false);
      });
    return () => {
      aborted = true;
    };
  }, [id, push]);

  if (loading) {
    return <p className="text-center text-sm text-muted">Chargement…</p>;
  }
  if (!detail) {
    return (
      <div className="card text-sm">
        Activité introuvable.
        <Link to="/activites" className="ml-2 text-accent">
          Retour
        </Link>
      </div>
    );
  }

  const a = detail.activity;
  const s = detail.streams;

  return (
    <div className="space-y-3">
      <button
        onClick={() => navigate(-1)}
        className="text-sm text-accent hover:underline"
      >
        ← Retour
      </button>

      <div className="card">
        <h2 className="text-lg font-medium">{a.name || "Activité"}</h2>
        <p className="text-xs text-muted">{new Date(a.date).toLocaleString("fr-FR")}</p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <MetricCard label="Distance" value={`${a.distance_km.toFixed(1)} km`} />
        <MetricCard label="Durée" value={formatHms(a.duration_sec)} />
        <MetricCard
          label="D+"
          value={a.elevation_m != null ? `${Math.round(a.elevation_m)} m` : "—"}
        />
        <MetricCard label="TSS" value={a.tss.toFixed(0)} />
        <MetricCard
          label="FC moy"
          value={a.avg_hr != null ? `${Math.round(a.avg_hr)} bpm` : "—"}
        />
        <MetricCard
          label="Puissance"
          value={a.avg_power != null ? `${Math.round(a.avg_power)} W` : "—"}
        />
      </div>

      {s.latlng && <ActivityMap latlng={s.latlng} />}

      <StreamChart
        title="Fréquence cardiaque (bpm)"
        time={s.time}
        values={s.heartrate}
        color="#ef4444"
        yKey="hr"
      />
      <StreamChart
        title="Altitude (m)"
        time={s.time}
        values={s.altitude}
        color="#22c55e"
        yKey="alt"
      />
      <StreamChart
        title="Puissance (W)"
        time={s.time}
        values={s.watts}
        color="#f97316"
        yKey="watts"
      />

      {detail.hr_zones && (
        <ZoneBar zones={detail.hr_zones as Record<string, number>} />
      )}

      <Link
        to={`/coach?prompt=${encodeURIComponent(
          `Analyse l'activité Strava avec strava_id=${a.strava_id}.`,
        )}`}
        className="btn-primary w-full"
      >
        🤖 Analyser avec le coach
      </Link>
    </div>
  );
}

interface ChartProps {
  title: string;
  time: number[] | null;
  values: number[] | null;
  color: string;
  yKey: string;
}

function StreamChart({ title, time, values, color, yKey }: ChartProps) {
  const data = buildSeries(time, values, yKey);
  if (data.length === 0) return null;
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-medium text-gray-200">{title}</h3>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <defs>
              <linearGradient id={`grad-${yKey}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.45} />
                <stop offset="95%" stopColor={color} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#2c313a" strokeDasharray="3 3" />
            <XAxis dataKey="t" stroke="#9aa3af" fontSize={11} />
            <YAxis stroke="#9aa3af" fontSize={11} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#23272f",
                border: "1px solid #2c313a",
                borderRadius: 8,
                color: "#e5e7eb",
              }}
            />
            <Area
              type="monotone"
              dataKey={yKey}
              stroke={color}
              fill={`url(#grad-${yKey})`}
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
