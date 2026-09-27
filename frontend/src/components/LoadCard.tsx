import type { LoadResponse } from "../api/types";
import LoadChart from "./LoadChart";
import StatStrip from "./StatStrip";

interface Props {
  load: LoadResponse | null;
}

function zoneTone(zone: string | undefined) {
  switch (zone) {
    case "freshness":
      return "good" as const;
    case "optimal":
      return "accent" as const;
    case "overreaching":
      return "warn" as const;
    case "overtraining":
      return "danger" as const;
    default:
      return "accent" as const;
  }
}

export default function LoadCard({ load }: Props) {
  const current = load?.current;
  return (
    <div className="card space-y-3">
      <h3 className="label-eyebrow">Évolution charge — CTL / ATL / TSB</h3>
      <StatStrip
        items={[
          {
            label: "CTL",
            value: current ? current.ctl.toFixed(1) : "—",
            hint: "Forme (42 j)",
          },
          {
            label: "ATL",
            value: current ? current.atl.toFixed(1) : "—",
            hint: "Fatigue (7 j)",
          },
          {
            label: "TSB",
            value: current ? current.tsb.toFixed(1) : "—",
            hint: "Fraîcheur",
            badge: current
              ? { label: current.zone_label_fr, tone: zoneTone(current.zone) }
              : undefined,
          },
        ]}
      />
      <LoadChart embedded data={load?.history || []} />
    </div>
  );
}
