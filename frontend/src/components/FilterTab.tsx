export default function FilterTab({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1 text-xs transition-colors ${
        active
          ? "border-accent/40 bg-accent/15 text-accent"
          : "border-border/[0.06] bg-overlay/[0.03] text-fg-soft hover:text-accent"
      }`}
    >
      {label}
    </button>
  );
}
