import { useEffect, useState } from "react";
import {
  VIEWING_EVENT,
  getViewingAthlete,
  getViewingAthleteName,
} from "../api/client";

export interface ViewingAthlete {
  id: string;
  name: string | null;
}

function read(): ViewingAthlete | null {
  const id = getViewingAthlete();
  return id ? { id, name: getViewingAthleteName() } : null;
}

/**
 * Athlète actuellement consulté par un coach (impersonation), ou `null`.
 * Réactif : se met à jour quand `set/clearViewingAthlete` est appelé (même
 * onglet via {@link VIEWING_EVENT}) ou depuis un autre onglet (`storage`).
 */
export function useViewing(): ViewingAthlete | null {
  const [viewing, setViewing] = useState<ViewingAthlete | null>(read);
  useEffect(() => {
    const sync = () => setViewing(read());
    window.addEventListener(VIEWING_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(VIEWING_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return viewing;
}
