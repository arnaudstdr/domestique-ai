// Fetch wrapper sans dépendance externe : base URL relative, erreurs typées.

import type {
  ActivitiesList,
  ActivityDetail,
  CoachMessage,
  CoachSession,
  LoadResponse,
  MorningEntry,
  MorningResponse,
  Objective,
  OvertrainingResponse,
  SyncResult,
  SyncStatus,
} from "./types";

const API_BASE = "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
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

export async function streamCoachChat(
  body: { session_id: string | null; message: string },
  onEvent: (event: CoachEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/coach/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, response.statusText);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
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
