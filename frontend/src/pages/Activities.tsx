import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ActivitiesList } from "../api/types";
import ActivityCard from "../components/ActivityCard";
import { useToast } from "../hooks/useToast";

const PAGE_SIZE = 20;

export default function Activities() {
  const [data, setData] = useState<ActivitiesList | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const { push } = useToast();

  useEffect(() => {
    let aborted = false;
    setLoading(true);
    api.activities
      .list(page, PAGE_SIZE)
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
  }, [page, push]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Activités</h2>
        {data && (
          <span className="text-xs text-muted">
            {data.total} au total
          </span>
        )}
      </div>

      {loading && !data && (
        <p className="text-center text-sm text-muted">Chargement…</p>
      )}

      {data?.items.length === 0 && !loading && (
        <div className="card text-sm text-muted">
          Aucune activité. Lance un sync Strava depuis le dashboard.
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
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            ← Précédent
          </button>
          <span className="text-sm text-muted">
            Page {page} / {totalPages}
          </span>
          <button
            className="btn-ghost"
            disabled={page >= totalPages || loading}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Suivant →
          </button>
        </div>
      )}
    </div>
  );
}
