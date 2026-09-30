import { useEffect, useState } from "react";
import { Info, Wrench } from "lucide-react";
import { api } from "../api/client";
import type { Announcement } from "../api/types";

/**
 * Bandeau plateforme non bloquant : maintenance ou message d'information.
 * Rendu uniquement si l'admin a défini une annonce. Silencieux en cas d'erreur.
 */
export default function AnnouncementBanner() {
  const [data, setData] = useState<Announcement | null>(null);

  useEffect(() => {
    api.announcement
      .get()
      .then(setData)
      .catch(() => undefined);
  }, []);

  if (!data || (!data.maintenance_mode && !data.message)) return null;

  const maintenance = data.maintenance_mode;
  const text = data.message || "Maintenance en cours — certaines fonctionnalités peuvent être indisponibles.";

  return (
    <div
      role="status"
      className={`border-b pt-[env(safe-area-inset-top)] ${
        maintenance
          ? "border-red-500/30 bg-red-500/10 text-red-300"
          : "border-accent/30 bg-accent/10 text-accent"
      }`}
    >
      <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-2 text-xs">
        {maintenance ? (
          <Wrench className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <Info className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        )}
        <span className="min-w-0">{text}</span>
      </div>
    </div>
  );
}
