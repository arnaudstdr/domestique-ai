import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  ActivitySummary,
  FtpProjectionResponse,
  LoadResponse,
  MorningResponse,
  Objective,
  OvertrainingResponse,
  DailyBriefResponse,
  RideVolumeResponse,
  WeeklyVolumeResponse,
} from "../api/types";
import ActivityCard from "../components/ActivityCard";
import DailyBriefCard from "../components/DailyBriefCard";
import LoadCard from "../components/LoadCard";
import ObjectiveCard from "../components/ObjectiveCard";
import RecoveryCard from "../components/RecoveryCard";
import VolumeCard from "../components/VolumeCard";
import { useMe } from "../hooks/useMe";
import { useToast } from "../hooks/useToast";

const TODAY_FR = new Intl.DateTimeFormat("fr-FR", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

function localTodayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function greeting(hour: number): string {
  if (hour < 6) return "Bonne nuit";
  if (hour < 12) return "Bonjour";
  if (hour < 18) return "Bon après-midi";
  return "Bonsoir";
}

export default function Dashboard() {
  const [load, setLoad] = useState<LoadResponse | null>(null);
  const [ot, setOt] = useState<OvertrainingResponse | null>(null);
  const [volume, setVolume] = useState<RideVolumeResponse | null>(null);
  const [weekly, setWeekly] = useState<WeeklyVolumeResponse | null>(null);
  const [morning, setMorning] = useState<MorningResponse | null>(null);
  const [objective, setObjective] = useState<Objective | null>(null);
  const [projection, setProjection] = useState<FtpProjectionResponse | null>(null);
  const [recentActivities, setRecentActivities] = useState<ActivitySummary[]>([]);
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const me = useMe();
  const { push } = useToast();

  const firstName = me?.display_name?.split(" ")[0] || me?.email?.split("@")[0] || null;

  async function refresh() {
    setLoading(true);
    try {
      const [l, o, vol, wk, morn, obj, proj, acts] = await Promise.all([
        api.metrics.load(90),
        api.metrics.overtraining(),
        api.metrics.rideVolume(),
        api.metrics.weeklyVolume(12),
        api.morning.get(14),
        api.objective.get(),
        api.metrics.ftpProjection(),
        api.activities.list(1, 20),
      ]);
      setLoad(l);
      setOt(o);
      setVolume(vol);
      setWeekly(wk);
      setMorning(morn);
      setObjective(obj);
      setProjection(proj);
      setRecentActivities(acts.items);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur de chargement : ${msg}`, "error");
    } finally {
      setLoading(false);
    }
  }

  async function refreshBrief() {
    // Le brief peut prendre quelques secondes (appel LLM). On le charge en
    // parallèle des autres widgets pour ne pas bloquer le rendu, et on
    // affiche un skeleton pendant ce temps.
    setBriefLoading(true);
    try {
      const b = await api.coach.dailyBrief();
      setBrief(b);
    } catch {
      // Silencieux : l'absence de brief ne doit pas perturber le Dashboard.
    } finally {
      setBriefLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    refreshBrief();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Les alertes overtraining remontent dans la surface unique du brief —
  // celles non déjà portées par l'alerte primaire sont affichées en dépliable.
  const secondaryAlerts = (ot?.alerts || [])
    .filter((a) => a.message !== brief?.primary_alert?.message)
    .map((a) => ({ message: a.message, level: a.level }));

  // Dernière sortie : l'API trie par date décroissante, l'item [0] est la plus
  // récente. On en déduit aussi le nombre de séances du jour (double séance).
  const latestActivity = recentActivities[0] ?? null;
  const todayIso = localTodayIso();
  const todayCount = recentActivities.filter(
    (a) => a.date.slice(0, 10) === todayIso,
  ).length;
  const activityIsToday = latestActivity?.date.slice(0, 10) === todayIso;

  return (
    <div className="stagger space-y-4">
      <div className="px-1">
        <h2 className="font-display text-xl font-extrabold tracking-tight">
          {greeting(new Date().getHours())}
          {firstName ? `, ${firstName}` : ""}
        </h2>
        <p className="text-xs text-muted capitalize">{TODAY_FR.format(new Date())}</p>
      </div>

      <DailyBriefCard
        data={brief}
        loading={briefLoading}
        secondaryAlerts={secondaryAlerts}
        indicators={ot?.indicators || null}
      />

      {/* Dernière sortie : boucle prévu (brief) → réalisé. */}
      {latestActivity ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <h3 className="label-eyebrow">
              {activityIsToday ? "Sortie du jour" : "Dernière sortie"}
            </h3>
            {todayCount > 1 && (
              <span className="pill bg-accent/10 text-accent">
                {todayCount} séances aujourd'hui
              </span>
            )}
          </div>
          <ActivityCard activity={latestActivity} />
        </div>
      ) : loading ? (
        <div className="card animate-pulse space-y-3">
          <div className="h-3 w-24 rounded bg-overlay/10" />
          <div className="h-16 w-full rounded bg-overlay/10" />
        </div>
      ) : null}

      {/* Rangée 2 colonnes (empilée sur mobile) : cap + récupération. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ObjectiveCard objective={objective} projection={projection} loading={loading} />
        <RecoveryCard morning={morning} loading={loading} />
      </div>

      <VolumeCard volume={volume} weeks={weekly?.weeks || []} />

      <LoadCard load={load} />

      <div className="flex justify-end">
        <Link
          to="/tendances"
          className="text-xs text-accent hover:underline"
        >
          Voir les tendances longues →
        </Link>
      </div>

      {loading && <p className="text-center text-sm text-muted">Chargement…</p>}
    </div>
  );
}
