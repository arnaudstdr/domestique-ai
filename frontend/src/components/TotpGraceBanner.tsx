import { Link, useLocation } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { useMe } from "../hooks/useMe";

/**
 * Bandeau de rappel 2FA pendant la période de grâce d'un nouveau compte.
 *
 * Non masquable : tant que le TOTP n'est pas activé et que la deadline court,
 * il reste affiché (style urgent dans les deux derniers jours). Une fois la
 * deadline passée, le middleware re-bloque l'app et redirige vers `/setup-2fa`.
 */
function daysLeft(deadline: string): number {
  const remaining = new Date(deadline).getTime() - Date.now();
  return Math.max(0, Math.ceil(remaining / 86_400_000));
}

export default function TotpGraceBanner() {
  const me = useMe();
  const location = useLocation();

  if (
    !me ||
    me.role === "admin" ||
    !me.has_password ||
    me.totp_enabled ||
    !me.totp_grace_until
  ) {
    return null;
  }

  const days = daysLeft(me.totp_grace_until);
  const urgent = days <= 2;

  return (
    <div className={`border-b ${urgent ? "border-red-500/30 bg-red-500/10" : "border-accent/20 bg-accent/10"}`}>
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-4 py-2">
        <span
          className={`flex min-w-0 items-center gap-2 text-xs ${urgent ? "text-red-400" : "text-accent"}`}
        >
          <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span className="truncate">
            Sécurise ton compte : active la double authentification
            {days > 0 ? ` — J-${days}` : " (dernier jour)"}.
          </span>
        </span>
        <Link
          to={`/setup-2fa?next=${encodeURIComponent(location.pathname)}`}
          className="btn-primary shrink-0 px-3 py-1.5 text-xs"
        >
          Activer
        </Link>
      </div>
    </div>
  );
}
