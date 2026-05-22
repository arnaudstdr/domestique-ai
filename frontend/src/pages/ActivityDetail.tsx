import { useEffect, useRef, useState } from "react";
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
import { Bot } from "lucide-react";
import { api, ApiError, streamCoachAnalyze } from "../api/client";
import type {
  ActivityDetail as ActivityDetailType,
  SimilarActivitiesResponse,
} from "../api/types";
import ActivityMap from "../components/ActivityMap";
import ChatBubble from "../components/ChatBubble";
import MetricCard from "../components/MetricCard";
import SimilarActivities from "../components/SimilarActivities";
import ZoneBar from "../components/ZoneBar";
import { useToast } from "../hooks/useToast";

const ANALYSIS_PROMPT = (sid: number) =>
  `Analyse l'activité Strava avec strava_id=${sid}. ` +
  `Étape 1 : appelle \`get_activity_details(strava_id=${sid})\` pour récupérer ` +
  `les chiffres (durée, distance, FC, charge, zones HR) ainsi que la date. ` +
  `Étape 2 : appelle \`get_training_load_state\` (CTL/ATL/TSB du jour) et ` +
  `\`get_objective\` pour le contexte. ` +
  `Étape 2 bis : appelle \`get_planned_workout(date=<date ISO de l'activité>)\` ` +
  `pour récupérer la séance qui était prévue ce jour-là dans le plan en cours. ` +
  `Compare le réalisé au prévu (kind, target_zone, durée, charge estimée) — ` +
  `ou note explicitement que la sortie était hors plan si aucun plan ne couvre ` +
  `cette date. ` +
  `Étape 3 : conclus en 4-6 lignes en répondant explicitement à : ` +
  `(1) ce que cette sortie a apporté physiologiquement (filière dominante, ` +
  `stimulus principal) ; ` +
  `(2) si elle a été *productive* ou *contre-productive* vu le TSB courant, ` +
  `l'objectif et la séance prévue (conforme au plan ? écart en intensité ou ` +
  `volume ? si écart, avec quel impact ?) ; ` +
  `(3) la séance ou la récup à privilégier ensuite. ` +
  `Sois concis, factuel, en français.`;

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

interface AnalysisState {
  content: string;
  thinking: string | null;
  toolCalls: { name: string; arguments: unknown; result: unknown }[];
}

const EMPTY_ANALYSIS: AnalysisState = {
  content: "",
  thinking: null,
  toolCalls: [],
};

export default function ActivityDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ActivityDetailType | null>(null);
  const [similar, setSimilar] = useState<SimilarActivitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisState | null>(null);
  const analysisRef = useRef<AnalysisState>(EMPTY_ANALYSIS);
  const { push } = useToast();

  async function runAnalysis() {
    if (analyzing || !detail) return;
    setAnalyzing(true);
    analysisRef.current = EMPTY_ANALYSIS;
    setAnalysis(EMPTY_ANALYSIS);
    try {
      await streamCoachAnalyze(
        ANALYSIS_PROMPT(detail.activity.strava_id),
        (event) => {
          if (event.type === "thinking") {
            analysisRef.current = {
              ...analysisRef.current,
              thinking: (analysisRef.current.thinking || "") + event.value,
            };
            setAnalysis({ ...analysisRef.current });
          } else if (event.type === "token") {
            analysisRef.current = {
              ...analysisRef.current,
              content: analysisRef.current.content + event.value,
            };
            setAnalysis({ ...analysisRef.current });
          } else if (event.type === "tool_call") {
            analysisRef.current = {
              ...analysisRef.current,
              toolCalls: [
                ...analysisRef.current.toolCalls,
                { name: event.name, arguments: event.args, result: null },
              ],
            };
            setAnalysis({ ...analysisRef.current });
          } else if (event.type === "tool_result") {
            analysisRef.current = {
              ...analysisRef.current,
              toolCalls: analysisRef.current.toolCalls.map((tc) =>
                tc.name === event.name && tc.result === null
                  ? { ...tc, result: event.result }
                  : tc,
              ),
            };
            setAnalysis({ ...analysisRef.current });
          } else if (event.type === "error") {
            push(event.value, "error");
          }
        },
      );
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Analyse : ${msg}`, "error");
    } finally {
      setAnalyzing(false);
    }
  }

  useEffect(() => {
    let aborted = false;
    setLoading(true);
    setSimilar(null);
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
    // Recherche d'activités similaires en parallèle (best-effort, ne bloque pas).
    api.activities
      .similar(Number(id))
      .then((s) => {
        if (!aborted) setSimilar(s);
      })
      .catch(() => {
        // Silencieux : l'absence de comparateur ne doit pas perturber la page.
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
        {a.avg_temp != null && (
          <MetricCard
            label="Température"
            value={`${a.avg_temp.toFixed(1)} °C`}
            hint={
              a.min_temp != null && a.max_temp != null
                ? `${a.min_temp.toFixed(0)} → ${a.max_temp.toFixed(0)} °C`
                : undefined
            }
          />
        )}
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
      <StreamChart
        title="Température (°C)"
        time={s.time}
        values={s.temp}
        color="#fbbf24"
        yKey="temp"
      />

      {detail.hr_zones && (
        <ZoneBar zones={detail.hr_zones as Record<string, number>} />
      )}

      {similar && <SimilarActivities data={similar} />}

      <button
        onClick={runAnalysis}
        disabled={analyzing}
        className="btn-primary w-full"
      >
        {analyzing && !analysis?.content ? (
          "Le coach analyse cette sortie…"
        ) : analyzing ? (
          "Réception de la réponse…"
        ) : (
          <span className="inline-flex items-center justify-center gap-2">
            <Bot className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {analysis ? "Relancer l'analyse" : "Analyser cette sortie"}
          </span>
        )}
      </button>

      {analysis && (analyzing || analysis.content || analysis.thinking) && (
        <div className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
            <Bot className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
            Analyse du coach
          </h3>
          <ChatBubble
            role="assistant"
            content={analysis.content || (analyzing ? "…" : "")}
            thinking={analysis.thinking}
            toolCalls={analysis.toolCalls}
          />
        </div>
      )}
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
