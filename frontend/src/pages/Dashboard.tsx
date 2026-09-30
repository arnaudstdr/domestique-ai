import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  FtpProjectionResponse,
  LoadResponse,
  MorningResponse,
  Objective,
  OvertrainingResponse,
  DailyBriefResponse,
  RideVolumeResponse,
  WeeklyVolumeResponse,
} from "../api/types";
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
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const me = useMe();
  const { push } = useToast();

  const firstName = me?.display_name?.split(" ")[0] || me?.email?.split("@")[0] || null;

  async function refresh() {
    setLoading(true);
    try {
      const [l, o, vol, wk, morn, obj, proj] = await Promise.all([
        api.metrics.load(90),
        api.metrics.overtraining(),
        api.metrics.rideVolume(),
        api.metrics.weeklyVolume(12),
        api.morning.get(14),
        api.objective.get(),
        api.metrics.ftpProjection(),
      ]);
      setLoad(l);
      setOt(o);
      setVolume(vol);
      setWeekly(wk);
      setMorning(morn);
      setObjective(obj);
      setProjection(proj);
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
