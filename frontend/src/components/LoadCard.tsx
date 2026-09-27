import type { LoadResponse } from "../api/types";
import LoadChart from "./LoadChart";
import StatStrip from "./StatStrip";

interface Props {
  load: LoadResponse | null;
}

export default function LoadCard({ load }: Props) {
  const current = load?.current;
  return (
    <div className="card space-y-3">
      <h3 className="label-eyebrow">Évolution charge — CTL / ATL</h3>
      <StatStrip
        columns={2}
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
        ]}
      />
      <LoadChart embedded data={load?.history || []} />
    </div>
  );
}
