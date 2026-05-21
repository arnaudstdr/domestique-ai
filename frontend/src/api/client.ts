// Fetch wrapper sans dépendance externe : base URL relative, erreurs typées.

import type {
  ActivitiesList,
  ActivityDetail,
  Availability,
  CoachMessage,
  CoachSession,
  FtpProjectionResponse,
  LoadResponse,
  MorningEntry,
  MorningResponse,
  Objective,
  OvertrainingResponse,
  PlanCreateRequest,
  PlanDetail,
  PlanSummary,
  Profile,
  RideVolumeResponse,
  SyncResult,
  SyncStatus,
  TodayWorkoutResponse,
  TrendPeriod,
  TrendsResponse,
} from "./types";

const API_BASE = "";
const TOKEN_KEY = "domestique_api_token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getApiToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setApiToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // localStorage indisponible (mode privé Safari, etc.) — l'utilisateur
    // devra ressaisir à chaque reload.
  }
}

export function clearApiToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // no-op
  }
}

function authHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Intercepte les 401 : nettoie le token et redirige vers /login.
 * Conserve le chemin courant en query (`?next=...`) pour rebondir après auth.
 */
function handleUnauthorized(): void {
  clearApiToken();
  const current = window.location.pathname + window.location.search;
  if (window.location.pathname !== "/login") {
    const next = encodeURIComponent(current);
    window.location.assign(`/login?next=${next}`);
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (response.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, "Unauthorized");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      message = data.detail || data.message || message;
    } catch {
      // payload non JSON, on garde le statusText
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  const ct = response.headers.get("content-type") || "";
  if (!ct.includes("application/json")) return undefined as T;
  return response.json() as Promise<T>;
}

// ---- Endpoints ---------------------------------------------------------------

export const api = {
  metrics: {
    load: (days = 90) =>
      http<LoadResponse>(`/api/metrics/load?days=${days}`),
    overtraining: () => http<OvertrainingResponse>(`/api/metrics/overtraining`),
    rideVolume: () => http<RideVolumeResponse>(`/api/metrics/ride-volume`),
    trends: (period: TrendPeriod = "6m") =>
      http<TrendsResponse>(`/api/metrics/trends?period=${period}`),
    ftpProjection: () =>
      http<FtpProjectionResponse>(`/api/metrics/ftp-projection`),
  },
  activities: {
    list: (page = 1, page_size = 20, days?: number) => {
      const q = new URLSearchParams({
        page: String(page),
        page_size: String(page_size),
      });
      if (days) q.set("days", String(days));
      return http<ActivitiesList>(`/api/activities?${q.toString()}`);
    },
    detail: (id: number) => http<ActivityDetail>(`/api/activities/${id}`),
  },
  morning: {
    get: (days = 90) => http<MorningResponse>(`/api/morning?days=${days}`),
    post: (entry: Partial<MorningEntry>) =>
      http<void>(`/api/morning`, {
        method: "POST",
        body: JSON.stringify(entry),
      }),
  },
  objective: {
    get: () => http<Objective | null>(`/api/objective`),
    put: (obj: Objective) =>
      http<Objective>(`/api/objective`, {
        method: "PUT",
        body: JSON.stringify(obj),
      }),
  },
  profile: {
    get: () => http<Profile | null>(`/api/profile`),
    put: (profile: Profile) =>
      http<Profile>(`/api/profile`, {
        method: "PUT",
        body: JSON.stringify(profile),
      }),
  },
  availability: {
    get: () => http<Availability | null>(`/api/availability`),
    put: (av: Availability) =>
      http<Availability>(`/api/availability`, {
        method: "PUT",
        body: JSON.stringify(av),
      }),
  },
  strava: {
    sync: () => http<SyncStatus>(`/api/strava/sync`, { method: "POST" }),
    syncStatus: () => http<SyncStatus>(`/api/strava/sync-status`),
    recalculate: () =>
      http<SyncResult>(`/api/strava/recalculate`, { method: "POST" }),
    backfillHrZones: () =>
      http<SyncResult>(`/api/strava/backfill-hr-zones`, { method: "POST" }),
  },
  coach: {
    sessions: () => http<CoachSession[]>(`/api/coach/sessions`),
    messages: (sessionId: string) =>
      http<CoachMessage[]>(`/api/coach/sessions/${sessionId}/messages`),
    deleteSession: (sessionId: string) =>
      http<void>(`/api/coach/sessions/${sessionId}`, { method: "DELETE" }),
    today: (availableMin?: number) => {
      const q = availableMin ? `?available_min=${availableMin}` : "";
      return http<TodayWorkoutResponse>(`/api/coach/today${q}`);
    },
  },
  plan: {
    list: (limit = 20) =>
      http<PlanSummary[]>(`/api/plan?limit=${limit}`),
    create: (payload: PlanCreateRequest) =>
      http<PlanDetail>(`/api/plan`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    detail: (id: number) => http<PlanDetail>(`/api/plan/${id}`),
    remove: (id: number) =>
      http<void>(`/api/plan/${id}`, { method: "DELETE" }),
    exportZip: async (id: number): Promise<{ blob: Blob; filename: string }> => {
      const response = await fetch(`${API_BASE}/api/plan/${id}/export.zip`, {
        headers: { ...authHeaders() },
      });
      if (response.status === 401) {
        handleUnauthorized();
        throw new ApiError(401, "Unauthorized");
      }
      if (!response.ok) {
        throw new ApiError(response.status, response.statusText);
      }
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/);
      const filename = match ? match[1] : `plan_${id}.zip`;
      return { blob: await response.blob(), filename };
    },
  },
};

// SSE chat — utilise fetch + ReadableStream pour pouvoir envoyer un body POST
// (EventSource ne supporte que GET).
export type CoachEvent =
  | { type: "session_id"; value: string }
  | { type: "thinking"; value: string }
  | { type: "token"; value: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | {
      type: "tool_result";
      name: string;
      result: Record<string, unknown>;
    }
  | { type: "error"; value: string }
  | { type: "done" };

async function consumeSseStream(
  path: string,
  body: unknown,
  onEvent: (event: CoachEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, "Unauthorized");
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, response.statusText);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    // sse-starlette utilise des fins de ligne CRLF par défaut. On normalise
    // pour que les délimiteurs marchent peu importe la convention serveur.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = chunk
        .split("\n")
        .find((l) => l.startsWith("data:"))
        ?.replace(/^data:\s?/, "");
      if (!dataLine) continue;
      try {
        const event = JSON.parse(dataLine) as CoachEvent;
        onEvent(event);
        if (event.type === "done") return;
      } catch {
        // ignore malformed frames
      }
    }
  }
}

export function streamCoachChat(
  body: { session_id: string | null; message: string },
  onEvent: (event: CoachEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return consumeSseStream("/api/coach/chat", body, onEvent, signal);
}

export function streamCoachAnalyze(
  prompt: string,
  onEvent: (event: CoachEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return consumeSseStream("/api/coach/analyze", { prompt }, onEvent, signal);
}

// ---- Garmin push -------------------------------------------------------------

export type GarminPushEvent =
  | { type: "start"; total: number }
  | {
      type: "progress";
      index: number;
      total: number;
      workout: { date: string; name: string };
    }
  | {
      type: "result";
      workout: { date: string; name: string };
      workout_id: number | null;
      scheduled: boolean;
      url?: string;
      error?: string;
    }
  | { type: "error"; value: string }
  | { type: "done"; uploaded: number; errors: number };

async function consumeGarminSse(
  path: string,
  body: unknown,
  onEvent: (event: GarminPushEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, "Unauthorized");
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, response.statusText);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = chunk
        .split("\n")
        .find((l) => l.startsWith("data:"))
        ?.replace(/^data:\s?/, "");
      if (!dataLine) continue;
      try {
        const event = JSON.parse(dataLine) as GarminPushEvent;
        onEvent(event);
        if (event.type === "done") return;
      } catch {
        // ignore malformed frames
      }
    }
  }
}

export function streamGarminPush(
  planId: number,
  schedule: boolean,
  onEvent: (event: GarminPushEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return consumeGarminSse(
    `/api/plan/${planId}/push-garmin`,
    { schedule },
    onEvent,
    signal,
  );
}
