// Types miroir des schémas Pydantic côté backend.

export type LoadZone = "freshness" | "optimal" | "overreaching" | "overtraining";

export interface LoadPoint {
  date: string;
  ctl: number;
  atl: number;
  tsb: number;
}

export interface LoadCurrent {
  ctl: number;
  atl: number;
  tsb: number;
  zone: LoadZone;
  zone_label_fr: string;
}

export interface LoadResponse {
  current: LoadCurrent | null;
  history: LoadPoint[];
}

export interface OvertrainingIndicators {
  chronic_tsb: number | null;
  monotony: number | null;
  strain: number | null;
  weekly_jump_pct: number | null;
}

export interface Alert {
  type: string;
  level: "warning" | "danger";
  message: string;
}

export interface OvertrainingResponse {
  alerts: Alert[];
  indicators: OvertrainingIndicators;
}

export interface VolumePeriod {
  distance_km: number;
  duration_sec: number;
}

export interface RideVolumeResponse {
  year: VolumePeriod;
  week: VolumePeriod;
}

export interface ActivitySummary {
  strava_id: number;
  name: string | null;
  date: string;
  distance_km: number;
  duration_sec: number;
  elevation_m: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  avg_power: number | null;
  tss: number;
  sport_type: string | null;
  hr_zones_sec: Record<string, number | null> | null;
}

export interface ActivitiesList {
  total: number;
  page: number;
  page_size: number;
  items: ActivitySummary[];
}

export interface ActivityStreams {
  time: number[] | null;
  heartrate: number[] | null;
  altitude: number[] | null;
  watts: number[] | null;
  latlng: [number, number][] | null;
  cadence: number[] | null;
  velocity_smooth: number[] | null;
  distance: number[] | null;
}

export interface ActivityDetail {
  activity: ActivitySummary;
  streams: ActivityStreams;
  hr_zones: Record<string, number> | null;
}

export interface MorningEntry {
  date: string;
  hrv_ms: number | null;
  resting_hr: number | null;
  sleep_hours: number | null;
  sleep_score: number | null;
  stress_score: number | null;
  notes: string | null;
}

export interface MorningBaseline {
  available: boolean;
  metric: string;
  baseline: number | null;
  latest: number | null;
  latest_date: string | null;
  delta_pct: number | null;
  sample_size: number | null;
  reason: string | null;
}

export interface MorningAlert {
  metric: string;
  delta_pct: number;
  baseline: number;
  latest: number;
  latest_date: string;
  severity: "warning" | "critical";
}

export interface MorningResponse {
  history: MorningEntry[];
  baselines: Record<string, MorningBaseline>;
  alerts: MorningAlert[];
}

export interface Objective {
  type: "cyclosportive" | "course" | "cyclo" | "maintenance";
  date: string | null;
  distance_km: number | null;
  elevation_m: number | null;
  target_ftp: number | null;
  target_avg_hr_zone: string | null;
  notes: string;
}

export interface SyncStatus {
  status: "idle" | "syncing" | "done" | "error";
  inserted: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface SyncResult {
  status: string;
  updated: number | null;
  inserted: number | null;
  error: string | null;
}

export interface CoachSession {
  session_id: string;
  started_at: string;
  messages: number;
  preview: string;
}

export interface CoachMessage {
  role: "user" | "assistant";
  content: string;
  thinking: string | null;
  tool_calls: { name: string; arguments: unknown; result: unknown }[] | null;
}

// ---- Plan d'entraînement -----------------------------------------------------

export type WorkoutPhase = "warmup" | "active" | "rest" | "cooldown";

export interface WorkoutStep {
  phase: WorkoutPhase;
  zone: string;
  duration_sec: number;
  repeat: number;
}

export interface Workout {
  date: string;
  name: string;
  sport: string;
  kind: string;
  duration_min: number;
  target_zone: string;
  structure: WorkoutStep[];
  estimated_tss: number;
  notes: string;
}

export interface PlanSummary {
  id: number;
  created_at: string;
  target_date: string | null;
  target_event_type: string | null;
  sessions_per_week: number | null;
  weeks: number | null;
}

export interface PlanDetail extends PlanSummary {
  workouts: Workout[];
}

export interface PlanCreateRequest {
  sessions_per_week: number;
  focus?: string | null;
}
