import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import type { MeResponse } from "../api/types";

interface MeContextValue {
  me: MeResponse | null;
  refresh: () => void;
}

const MeContext = createContext<MeContextValue>({
  me: null,
  refresh: () => undefined,
});

// Cache module-level : un seul appel `/me` partagé par toutes les pages, et
// dédoublonné même sous StrictMode (double-montage en dev).
let _cache: MeResponse | null = null;
let _inflight: Promise<MeResponse | null> | null = null;

function fetchMe(): Promise<MeResponse | null> {
  if (_cache) return Promise.resolve(_cache);
  if (!_inflight) {
    _inflight = api.auth
      .me()
      .then((m) => {
        _cache = m;
        return m;
      })
      .catch(() => null)
      .finally(() => {
        _inflight = null;
      });
  }
  return _inflight;
}

/** Fournit l'identité du compte courant. Monté dans la coquille authentifiée. */
export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(_cache);

  useEffect(() => {
    let alive = true;
    fetchMe().then((m) => {
      if (alive) setMe(m);
    });
    return () => {
      alive = false;
    };
  }, []);

  function refresh() {
    _cache = null;
    fetchMe().then(setMe);
  }

  return (
    <MeContext.Provider value={{ me, refresh }}>{children}</MeContext.Provider>
  );
}

/** Identité du compte courant (rôle compris). `null` tant que non chargée / en erreur. */
export function useMe(): MeResponse | null {
  return useContext(MeContext).me;
}
