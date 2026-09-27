interface Props {
  size?: number;
}

/**
 * Présence visuelle du coach : monogramme « D » avec halo pulsant discret.
 * Le halo est coupé sous `prefers-reduced-motion` (cf. index.css).
 */
export default function CoachAvatar({ size = 32 }: Props) {
  return (
    <span
      className="relative inline-grid place-items-center shrink-0"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <span className="absolute inset-0 rounded-xl bg-accent/25 blur-md animate-coach-halo" />
      <span
        className="relative grid place-items-center rounded-xl bg-accent font-display text-[15px] font-extrabold text-surface ring-1 ring-accent/50"
        style={{ width: size, height: size }}
      >
        D
      </span>
    </span>
  );
}
