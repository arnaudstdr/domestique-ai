import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { ActivitiesList, ActivityFilters as Filters } from "../api/types";
import ActivityCard from "../components/ActivityCard";
import ActivityFilters from "../components/ActivityFilters";
import { useToast } from "../hooks/useToast";

const PAGE_SIZE = 20;
const FETCH_DEBOUNCE_MS = 300;

const NUMERIC_KEYS: (keyof Filters)[] = [
  "distance_min_km",
  "distance_max_km",
  "elevation_min_m",
  "elevation_max_m",
  "duration_min_sec",
  "duration_max_sec",
  "tss_min",
  "tss_max",
];

function parseFiltersFromUrl(params: URLSearchParams): Filters {
  const f: Filters = {};
  const from = params.get("date_from");
  if (from) f.date_from = from;
  const to = params.get("date_to");
  if (to) f.date_to = to;
  const sports = params.getAll("sport_types");
  if (sports.length > 0) f.sport_types = sports;
  for (const k of NUMERIC_KEYS) {
    const raw = params.get(k);
    if (raw != null && raw !== "") {
      const n = Number(raw);
      if (Number.isFinite(n)) f[k] = n as never;
    }
  }
  return f;
}

function filtersToUrl(f: Filters, page: number): URLSearchParams {
  const p = new URLSearchParams();
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  for (const s of f.sport_types ?? []) p.append("sport_types", s);
  for (const k of NUMERIC_KEYS) {
    const v = f[k];
    if (v != null && !Number.isNaN(v as number)) p.set(k, String(v));
  }
  if (page > 1) p.set("page", String(page));
  return p;
}

export default function Activities() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<ActivitiesList | null>(null);
  const [sportTypes, setSportTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const { push } = useToast();

  const filters = useMemo(
    () => parseFiltersFromUrl(searchParams),
    [searchParams],
  );
  const page = Math.max(1, Number(searchParams.get("page") ?? "1"));

  // Charge la liste des sport_types une seule fois — alimente les chips.
  useEffect(() => {
    let aborted = false;
    api.activities
      .sportTypes()
      .then((types) => {
        if (!aborted) setSportTypes(types);
      })
      .catch(() => {
        /* silencieux : pas critique */
      });
    return () => {
      aborted = true;
    };
  }, []);

  // Debounce du fetch sur changement de filtres/page pour absorber les
  // saisies rapides dans les inputs numériques.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setLoading(true);
    debounceRef.current = setTimeout(() => {
      let aborted = false;
      api.activities
        .list(page, PAGE_SIZE, filters)
        .then((res) => {
          if (!aborted) setData(res);
        })
        .catch((err) => {
          const msg = err instanceof ApiError ? err.message : String(err);
          push(`Erreur : ${msg}`, "error");
        })
        .finally(() => {
          if (!aborted) setLoading(false);
        });
      return () => {
        aborted = true;
      };
    }, FETCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [filters, page, push]);

  const updateFilters = (next: Filters) => {
    // Tout changement de filtre reset la pagination à la page 1.
    setSearchParams(filtersToUrl(next, 1), { replace: false });
  };

  const clearFilters = () => {
    setSearchParams(new URLSearchParams(), { replace: false });
  };

  const setPage = (next: number) => {
    setSearchParams(filtersToUrl(filters, next), { replace: false });
  };

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Activités</h2>
        {data && (
          <span className="text-xs text-muted">{data.total} au total</span>
        )}
      </div>

      <ActivityFilters
        value={filters}
        sportTypes={sportTypes}
        onChange={updateFilters}
        onClear={clearFilters}
      />

      {loading && !data && (
        <p className="text-center text-sm text-muted">Chargement…</p>
      )}

      {data?.items.length === 0 && !loading && (
        <div className="card text-sm text-muted">
          Aucune activité ne correspond aux filtres.
        </div>
      )}

      <ul className="space-y-2">
        {data?.items.map((a) => (
          <li key={a.strava_id}>
            <ActivityCard activity={a} />
          </li>
        ))}
      </ul>

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between pt-2">
          <button
            className="btn-ghost"
            disabled={page <= 1 || loading}
            onClick={() => setPage(Math.max(1, page - 1))}
          >
            ← Précédent
          </button>
          <span className="text-sm text-muted">
            Page {page} / {totalPages}
          </span>
          <button
            className="btn-ghost"
            disabled={page >= totalPages || loading}
            onClick={() => setPage(Math.min(totalPages, page + 1))}
          >
            Suivant →
          </button>
        </div>
      )}
    </div>
  );
}
