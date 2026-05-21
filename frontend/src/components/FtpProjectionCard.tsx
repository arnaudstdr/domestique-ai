import type { FtpProjectionResponse } from "../api/types";

interface Props {
  data: FtpProjectionResponse;
}

const CONFIDENCE_LABELS: Record<FtpProjectionResponse["confidence"], string> = {
  low: "faible",
  medium: "moyenne",
  high: "élevée",
};

const CONFIDENCE_TONE: Record<FtpProjectionResponse["confidence"], string> = {
  low: "text-muted bg-white/5",
  medium: "text-yellow-300 bg-yellow-500/10",
  high: "text-emerald-300 bg-emerald-500/10",
};

function formatFtp(value: number | null): string {
  return value == null ? "—" : `${Math.round(value)} W`;
}

export default function FtpProjectionCard({ data }: Props) {
  const trendArrow = data.delta_pct > 0 ? "↗" : data.delta_pct < 0 ? "↘" : "→";
  const trendTone =
    data.delta_pct > 0 ? "text-emerald-300" : data.delta_pct < 0 ? "text-orange-300" : "text-muted";
  return (
    <div className="card space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-gray-200">Projection FTP — 4 semaines</h3>
        <span
          className={`pill ${CONFIDENCE_TONE[data.confidence]}`}
          title="Confiance qualitative (profondeur d'historique + stimulus Z4-Z5 plausible)"
        >
          confiance {CONFIDENCE_LABELS[data.confidence]}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-muted">Actuelle</div>
          <div className="text-2xl font-semibold text-gray-100">{formatFtp(data.current_ftp)}</div>
        </div>
        <div className={`text-center self-end pb-1 ${trendTone}`}>
          <div className="text-3xl leading-none">{trendArrow}</div>
          <div className="text-sm font-medium">{data.delta_pct.toFixed(2)} %</div>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-muted">Projetée</div>
          <div className="text-2xl font-semibold text-accent">{formatFtp(data.projected_ftp)}</div>
        </div>
      </div>

      <div className="rounded-lg bg-white/5 p-3 text-xs text-muted space-y-1">
        <div className="font-medium text-gray-300">Heuristique</div>
        <p>
          +1 % de FTP par +5 points de CTL net sur 28 jours, plafonné à ±5 %.
          La confiance monte à « élevée » si la part Z4-Z5 reste entre 4 % et 25 %
          avec au moins 60 jours d'historique.
        </p>
        <div className="pt-1 flex flex-wrap gap-x-4 gap-y-1">
          <span>
            ΔCTL 28 j :{" "}
            <span className="text-gray-200">
              {data.delta_ctl_28d == null ? "—" : data.delta_ctl_28d.toFixed(2)}
            </span>
          </span>
          <span>
            CTL :{" "}
            <span className="text-gray-200">
              {data.ctl_current == null ? "—" : data.ctl_current.toFixed(1)}
            </span>
          </span>
          <span>
            Z4-Z5 :{" "}
            <span className="text-gray-200">
              {data.z4_z5_share_pct == null ? "—" : `${data.z4_z5_share_pct.toFixed(1)} %`}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}
