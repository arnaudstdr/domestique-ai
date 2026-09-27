import { useEffect, useRef, useState } from "react";

interface Props {
  value: number | null;
  zone: string | null;
  size?: number;
  label?: string;
}

// Couleur d'état : cohérente avec les tons texte déjà utilisés dans l'app
// (emerald Frais, lime Optimal, ambre Fatigué, rouge Surentraîné).
export function zoneColor(zone: string | null | undefined): string {
  switch (zone) {
    case "Frais":
      return "#34d399";
    case "Optimal":
      return "#c7f24a";
    case "Fatigué":
      return "#f5a524";
    case "Surentraîné":
      return "#ff5d5d";
    default:
      return "#8e96a4";
  }
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Anneau TSB « instrument ». Le remplissage est animé au montage (0 → valeur),
 * proportionnel à la position du TSB dans la plage [-30, +30]. Rendu SVG pur,
 * couleur pilotée par la zone d'état — aucune lib de charts nécessaire.
 */
export default function TsbGauge({ value, zone, size = 108, label = "TSB" }: Props) {
  const color = zoneColor(zone);
  const R = 46;
  const CIRC = 2 * Math.PI * R;
  // TSB [-30, +30] → fraction [0, 1].
  const fraction = value == null ? 0 : Math.min(1, Math.max(0, (value + 30) / 60));
  const target = fraction * CIRC;

  const [offset, setOffset] = useState(() => (prefersReducedMotion() ? target : CIRC));
  const [count, setCount] = useState(() => (prefersReducedMotion() ? value : value != null ? 0 : null));
  const raf = useRef<number | null>(null);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setOffset(target);
      setCount(value);
      return;
    }
    const start = performance.now();
    const duration = 900;
    const from = CIRC;
    const fromCount = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setOffset(from + (target - from) * eased);
      if (value != null) setCount(fromCount + (value - fromCount) * eased);
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [target, value]);

  const display = count == null ? "—" : `${count >= 0 ? "+" : ""}${count.toFixed(1)}`;

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`TSB ${display}${zone ? ` (${zone})` : ""}`}
    >
      <svg viewBox="0 0 108 108" width={size} height={size} className="-rotate-90">
        <circle
          cx="54"
          cy="54"
          r={R}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth="8"
        />
        <circle
          cx="54"
          cy="54"
          r={R}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={CIRC}
          strokeDashoffset={offset}
          style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="label-eyebrow text-[10px]">{label}</span>
        <span
          className="metric-num text-2xl font-semibold leading-none"
          style={{ color }}
        >
          {display}
        </span>
        {zone && <span className="mt-0.5 text-[11px] text-muted">{zone}</span>}
      </div>
    </div>
  );
}
