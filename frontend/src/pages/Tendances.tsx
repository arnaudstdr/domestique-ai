import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  FtpProjectionResponse,
  TrendPeriod,
  TrendsResponse,
} from "../api/types";
import FtpProjectionCard from "../components/FtpProjectionCard";
import LongTermLoadChart from "../components/LongTermLoadChart";
import MonthlyVolumeChart from "../components/MonthlyVolumeChart";
import ZoneDistributionChart from "../components/ZoneDistributionChart";
import { useToast } from "../hooks/useToast";

const PERIODS: { value: TrendPeriod; label: string }[] = [
  { value: "3m", label: "3 mois" },
  { value: "6m", label: "6 mois" },
  { value: "1y", label: "1 an" },
  { value: "all", label: "Tout" },
];

export default function Tendances() {
  const [period, setPeriod] = useState<TrendPeriod>("6m");
  const [trends, setTrends] = useState<TrendsResponse | null>(null);
  const [ftp, setFtp] = useState<FtpProjectionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const { push } = useToast();

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [t, f] = await Promise.all([
          api.metrics.trends(period),
          api.metrics.ftpProjection(),
        ]);
        if (cancelled) return;
        setTrends(t);
        setFtp(f);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof ApiError ? err.message : String(err);
        push(`Erreur de chargement : ${msg}`, "error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [period, push]);

  return (
    <div className="stagger space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-display text-2xl font-extrabold tracking-tight text-gray-50">
          Tendances longues
        </h2>
        <div className="flex rounded-xl overflow-hidden border border-white/[0.08] text-xs">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setPeriod(p.value)}
              className={`px-3 py-1.5 font-semibold transition-colors ${
                period === p.value
                  ? "bg-accent text-surface"
                  : "bg-white/[0.04] text-muted hover:bg-white/[0.08]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {ftp && <FtpProjectionCard data={ftp} />}

      {trends && (
        <>
          <LongTermLoadChart
            data={trends.load_history}
            resolution={trends.resolution}
          />
          <MonthlyVolumeChart data={trends.monthly} />
          <ZoneDistributionChart data={trends.monthly} />
        </>
      )}

      {loading && !trends && (
        <p className="text-center text-sm text-muted">Chargement…</p>
      )}
    </div>
  );
}
