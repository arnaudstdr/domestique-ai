// Fetch wrapper sans dépendance externe : base URL relative, erreurs typées.

import type {
  AcceptInviteResponse,
  ActivitiesList,
  ActivityDetail,
  ActivityFilters,
  AthleteSummary,
  Availability,
  CoachMessage,
  CoachSession,
  DailyBriefResponse,
  FtpProjectionResponse,
  InvitationCreated,
  InvitationOut,
  LoadResponse,
  MeResponse,
  MorningEntry,
  MorningResponse,
  Objective,
  OvertrainingResponse,
  PlanCreateRequest,
  PlanDetail,
  PlanSummary,
  PrescriptionCreate,
  PrescriptionOut,
  Profile,
  RideVolumeResponse,
  SimilarActivitiesResponse,
  StravaAuthorize,
  StravaConnection,
  SyncResult,
  SyncStatus,
  TodayWorkoutResponse,
  TrendPeriod,
  TrendsResponse,
} from "./types";

const API_BASE = "";
const TOKEN_KEY = "domestique_api_token";
const VIEWING_KEY = "domestique_viewing_athlete";
const VIEWING_NAME_KEY = "domestique_viewing_athlete_name";

/** Événement émis quand l'athlète consulté change (pour rafraîchir l'UI). */
export const VIEWING_EVENT = "domestique:viewing-changed";

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

// --- Consultation d'un athlète par un coach (impersonation lecture seule) -----
// Quand un coach « entre » dans un athlète, son public_id est mémorisé ici et
// propagé à toutes les requêtes data via le query param `?athlete=`.

export function getViewingAthlete(): string | null {
  try {
    return localStorage.getItem(VIEWING_KEY);
  } catch {
    return null;
  }
}

export function getViewingAthleteName(): string | null {
  try {
    return localStorage.getItem(VIEWING_NAME_KEY);
  } catch {
    return null;
  }
}

export function setViewingAthlete(publicId: string, name?: string | null): void {
  try {
    localStorage.setItem(VIEWING_KEY, publicId);
    if (name) localStorage.setItem(VIEWING_NAME_KEY, name);
    else localStorage.removeItem(VIEWING_NAME_KEY);
  } catch {
    // no-op
  }
  window.dispatchEvent(new Event(VIEWING_EVENT));
}

export function clearViewingAthlete(): void {
  try {
    localStorage.removeItem(VIEWING_KEY);
    localStorage.removeItem(VIEWING_NAME_KEY);
  } catch {
    // no-op
  }
  window.dispatchEvent(new Event(VIEWING_EVENT));
}

/**
 * Ajoute `?athlete=<public_id>` au chemin quand un athlète est consulté, sauf
 * pour les routes d'identité (`/api/auth/*`) qui restent sur le compte courant.
 */
function withAthlete(path: string): string {
  const viewing = getViewingAthlete();
  // /api/auth/* vise le compte courant ; /api/roster/* porte déjà le public_id
  // cible dans son path — ni l'un ni l'autre ne prend le param ?athlete=.
  if (!viewing || path.startsWith("/api/auth/") || path.startsWith("/api/roster/")) {
    return path;
  }
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}athlete=${encodeURIComponent(viewing)}`;
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
  const response = await fetch(`${API_BASE}${withAthlete(path)}`, {
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
    list: (
      page = 1,
      page_size = 20,
      filters: ActivityFilters = {},
    ) => {
      const q = new URLSearchParams({
        page: String(page),
        page_size: String(page_size),
      });
      if (filters.days) q.set("days", String(filters.days));
      if (filters.date_from) q.set("date_from", filters.date_from);
      if (filters.date_to) q.set("date_to", filters.date_to);
      for (const s of filters.sport_types ?? []) {
        if (s) q.append("sport_types", s);
      }
      const numKeys: (keyof ActivityFilters)[] = [
        "distance_min_km",
        "distance_max_km",
        "elevation_min_m",
        "elevation_max_m",
        "duration_min_sec",
        "duration_max_sec",
        "tss_min",
        "tss_max",
      ];
      for (const k of numKeys) {
        const v = filters[k];
        if (v !== undefined && v !== null && !Number.isNaN(v as number)) {
          q.set(k, String(v));
        }
      }
      return http<ActivitiesList>(`/api/activities?${q.toString()}`);
    },
    sportTypes: () => http<string[]>(`/api/activities/sport-types`),
    detail: (id: number) => http<ActivityDetail>(`/api/activities/${id}`),
    similar: (id: number, limit = 10) =>
      http<SimilarActivitiesResponse>(
        `/api/activities/${id}/similar?limit=${limit}`,
      ),
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
    connection: () => http<StravaConnection>(`/api/strava/connection`),
    authorize: () => http<StravaAuthorize>(`/api/strava/authorize`),
    recalculate: () =>
      http<SyncResult>(`/api/strava/recalculate`, { method: "POST" }),
    backfillHrZones: () =>
      http<SyncResult>(`/api/strava/backfill-hr-zones`, { method: "POST" }),
    backfillTemperature: () =>
      http<SyncResult>(`/api/strava/backfill-temperature`, { method: "POST" }),
    backfillPolylines: () =>
      http<SyncResult>(`/api/strava/backfill-polylines`, { method: "POST" }),
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
    dailyBrief: (refresh = false) =>
      http<DailyBriefResponse>(
        `/api/coach/daily-brief${refresh ? "?refresh=true" : ""}`,
      ),
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
      const response = await fetch(`${API_BASE}${withAthlete(`/api/plan/${id}/export.zip`)}`, {
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
    exportIcs: async (id: number): Promise<{ blob: Blob; filename: string }> => {
      const response = await fetch(`${API_BASE}${withAthlete(`/api/plan/${id}/export.ics`)}`, {
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
      const filename = match ? match[1] : `plan_${id}.ics`;
      return { blob: await response.blob(), filename };
    },
  },
  auth: {
    me: () => http<MeResponse>(`/api/auth/me`),
    acceptInvite: (inviteToken: string, displayName?: string | null) =>
      http<AcceptInviteResponse>(`/api/auth/accept-invite`, {
        method: "POST",
        body: JSON.stringify({
          invite_token: inviteToken,
          display_name: displayName || null,
        }),
      }),
    logout: () => http<{ status: string }>(`/api/auth/logout`, { method: "POST" }),
    athletes: () => http<AthleteSummary[]>(`/api/auth/athletes`),
    createInvitation: (
      role: "coach" | "athlete" = "athlete",
      expiresInDays?: number | null,
    ) =>
      http<InvitationCreated>(`/api/auth/invitations`, {
        method: "POST",
        body: JSON.stringify({
          role,
          expires_in_days: expiresInDays ?? null,
        }),
      }),
    listInvitations: () => http<InvitationOut[]>(`/api/auth/invitations`),
    revokeInvitation: (id: number) =>
      http<void>(`/api/auth/invitations/${id}`, { method: "DELETE" }),
  },
  // Prescriptions vues par l'athlète courant (ou le coach en consultation).
  prescriptions: {
    list: () => http<PrescriptionOut[]>(`/api/prescriptions`),
  },
  // Écritures coach → espace athlète (public_id dans le path, pas de withAthlete).
  roster: {
    listPrescriptions: (publicId: string) =>
      http<PrescriptionOut[]>(`/api/roster/athletes/${publicId}/prescriptions`),
    prescribe: (publicId: string, body: PrescriptionCreate) =>
      http<PrescriptionOut>(`/api/roster/athletes/${publicId}/prescriptions`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    deletePrescription: (publicId: string, pid: number) =>
      http<void>(`/api/roster/athletes/${publicId}/prescriptions/${pid}`, {
        method: "DELETE",
      }),
    assignPlan: (publicId: string, body: PlanCreateRequest) =>
      http<PlanDetail>(`/api/roster/athletes/${publicId}/plan`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
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

// ---- LLM plan generation -----------------------------------------------------

import type { Workout } from "./types";

export type LlmPlanEvent =
  | {
      type: "start";
      target_date: string | null;
      target_event_type: string;
      ctl_current: number;
    }
  | {
      type: "week_completed";
      index: number;
      source: "llm" | "fallback";
      adjustments: string[];
      workouts: Workout[];
    }
  | {
      type: "error";
      value: string;
    }
  | {
      type: "done";
      plan_id: number | null;
      total_workouts?: number;
      llm_weeks?: number;
      fallback_weeks?: number;
    };

async function consumeLlmPlanSse(
  body: { sessions_per_week: number; focus: string | null },
  onEvent: (event: LlmPlanEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/plan/llm`, {
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
        const event = JSON.parse(dataLine) as LlmPlanEvent;
        onEvent(event);
        if (event.type === "done") return;
      } catch {
        // ignore malformed frames
      }
    }
  }
}

export function streamLlmPlan(
  body: { sessions_per_week: number; focus: string | null },
  onEvent: (event: LlmPlanEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return consumeLlmPlanSse(body, onEvent, signal);
}
