import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MeResponse } from "../api/types";

/** Identité du compte courant (rôle compris). `null` tant que non chargée / en erreur. */
export function useMe(): MeResponse | null {
  const [me, setMe] = useState<MeResponse | null>(null);
  useEffect(() => {
    let alive = true;
    api.auth
      .me()
      .then((m) => alive && setMe(m))
      .catch(() => alive && setMe(null));
    return () => {
      alive = false;
    };
  }, []);
  return me;
}
