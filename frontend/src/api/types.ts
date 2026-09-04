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

// ---- Activités similaires ----------------------------------------------------

export type SportBucket = "indoor" | "outdoor" | "other";

export interface SimilarActivityMatch {
  external_id: number;
  date: string;
  duration_sec: number | null;
  avg_heart_rate: number | null;
  avg_power: number | null;
  elevation_m: number;
  distance_km: number;
  training_load: number | null;
  duration_delta_pct: number | null;
  tss_delta_pct: number | null;
  power_delta_pct: number | null;
}

export interface SimilarActivitiesReference {
  external_id: number;
  date: string;
  distance_km: number;
  elevation_m: number;
  duration_sec: number | null;
  training_load: number | null;
  sport_bucket: SportBucket;
}

export interface SimilarActivitiesCriteria {
  distance_tolerance_pct: number;
  elevation_tolerance_pct: number;
  sport_bucket: SportBucket;
}

export interface SimilarActivitiesResponse {
  available: boolean;
  reason: string | null;
  reference: SimilarActivitiesReference | null;
  matches: SimilarActivityMatch[];
  criteria: SimilarActivitiesCriteria | null;
}

// ---- Tendances longues -------------------------------------------------------

export type TrendPeriod = "3m" | "6m" | "1y" | "all";
export type TrendResolution = "day" | "week" | "month";

export interface TrendLoadPoint {
  date: string;
  ctl: number;
  atl: number;
  tsb: number;
}

export interface TrendMonthlyEntry {
  month: string; // "YYYY-MM"
  distance_km: number;
  elevation_m: number;
  duration_sec: number;
  sessions: number;
  tss: number;
  distance_km_n1: number | null;
  tss_n1: number | null;
  z1_pct: number | null;
  z2_pct: number | null;
  z3_pct: number | null;
  z4_pct: number | null;
  z5_pct: number | null;
}

export interface TrendsResponse {
  period: TrendPeriod;
  resolution: TrendResolution;
  load_history: TrendLoadPoint[];
  monthly: TrendMonthlyEntry[];
}

export interface FtpProjectionResponse {
  current_ftp: number | null;
  projected_ftp: number | null;
  delta_pct: number;
  delta_ctl_28d: number | null;
  ctl_current: number | null;
  z4_z5_share_pct: number | null;
  confidence: "low" | "medium" | "high";
  history_days: number;
}

export interface ActivitySummary {
  external_id: number;
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
  avg_temp: number | null;
  min_temp: number | null;
  max_temp: number | null;
  map_polyline: string | null;
  calories: number | null;
  max_power: number | null;
  cadence_avg: number | null;
  cadence_max: number | null;
  speed_avg_kmh: number | null;
  speed_max_kmh: number | null;
  elevation_loss: number | null;
  source: string;
}

export interface ActivitiesList {
  total: number;
  page: number;
  page_size: number;
  items: ActivitySummary[];
}

export interface ActivityFilters {
  days?: number;
  date_from?: string;
  date_to?: string;
  sport_types?: string[];
  distance_min_km?: number;
  distance_max_km?: number;
  elevation_min_m?: number;
  elevation_max_m?: number;
  duration_min_sec?: number;
  duration_max_sec?: number;
  tss_min?: number;
  tss_max?: number;
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
  temp: number[] | null;
}

export interface ActivityDetail {
  activity: ActivitySummary;
  streams: ActivityStreams;
  hr_zones: Record<string, number> | null;
}

export interface ActivityWeather {
  available: boolean;
  issue_date: string | null;
  temp_c: number | null;
  apparent_temp_c: number | null;
  dew_point_c: number | null;
  relative_humidity_pct: number | null;
  wind_direction_deg: number | null;
  wind_compass: string | null;
  description: string | null;
  station: string | null;
}

export interface MorningEntry {
  date: string;
  hrv_ms: number | null;
  resting_hr: number | null;
  sleep_hours: number | null;
  sleep_score: number | null;
  stress_score: number | null;
  notes: string | null;
  spo2_avg_pct: number | null;
  respiratory_rate_avg_bpm: number | null;
  skin_temp_delta_c: number | null;
  sleep_deep_min: number | null;
  sleep_rem_min: number | null;
  sleep_light_min: number | null;
  sleep_awake_min: number | null;
  steps: number | null;
  active_calories: number | null;
  readiness_score: number | null;
  sleep_score_computed: number | null;
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

export interface GoogleHealthStatusResponse {
  configured: boolean;
  authenticated: boolean;
  last_sync_at: string | null;
}

export interface GoogleHealthSyncResponse {
  success: boolean;
  synced_dates: string[];
  skipped_dates: string[];
  message: string;
}

export interface GoogleHealthAuthResponse {
  auth_url: string;
}

export interface Objective {
  type: "cyclosportive" | "course" | "cyclo" | "forme" | "maintenance";
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

export interface GarminStatus {
  credentials: boolean;
  tokens: boolean;
  connected: boolean;
  sync: SyncStatus;
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
  title: string | null;
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
  uid?: string;
}

export interface PlanSummary {
  id: number;
  created_at: string;
  target_date: string | null;
  target_event_type: string | null;
  sessions_per_week: number | null;
  weeks: number | null;
  status?: string;
  parent_plan_id?: number | null;
  start_date?: string | null;
  adapt_reason?: string | null;
}

export interface PlanDetail extends PlanSummary {
  workouts: Workout[];
}

export interface PlanCreateRequest {
  sessions_per_week: number;
  focus?: string | null;
}

export type PlanDecisionValue = "planned" | "adjusted" | "rest";

export interface PlanDecision {
  id: number;
  plan_id: number;
  date: string;
  decision: PlanDecisionValue;
  workout?: Workout | null;
  reason: string;
  decided_by: string;
  created_at: string;
}

export interface WeeklyReviewResult {
  skipped: boolean;
  week_key?: string | null;
  decision: string;
  volume_factor: number;
  reason: string;
  replanned: boolean;
  new_plan_id?: number | null;
  parent_plan_id?: number | null;
  sessions_count?: number | null;
  error?: boolean;
  report?: Record<string, unknown>;
}

// ---- Profil utilisateur ------------------------------------------------------

export interface Profile {
  ftp: number | null;
  hr_rest: number | null;
  hr_max: number | null;
  sex: "M" | "F";
  lthr_pct: number;
}

// ---- Disponibilité hebdomadaire ---------------------------------------------

export type WeekdayName =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export interface DayAvailability {
  max_duration_min: number;
  context: "indoor" | "outdoor";
}

export interface AvailabilityPreferences {
  long_endurance_day: WeekdayName | null;
  intervals_day: WeekdayName | null;
}

export interface Availability {
  days: Partial<Record<WeekdayName, DayAvailability>>;
  preferences: AvailabilityPreferences | null;
}

// ---- Briefing quotidien (palier 1 proactivité) -------------------------------

export interface DailyBriefAlert {
  type: string;
  severity: "warning" | "danger";
  message: string;
}

export interface DailyBriefWorkout {
  rest_day: boolean;
  reason: string | null;
  kind: string | null;
  duration_min: number | null;
  name: string | null;
  target_zone: string | null;
  estimated_tss: number | null;
  structure: WorkoutStep[];
  notes: string | null;
}

export interface DailyBriefResponse {
  date: string;
  summary: string;
  tsb: number | null;
  tsb_zone: string | null;
  primary_alert: DailyBriefAlert | null;
  today_workout: DailyBriefWorkout;
  source: "cache" | "llm" | "fallback";
  morning_decision?: string | null;
  morning_reason?: string | null;
  morning_persisted?: boolean;
  sleep_hours?: number | null;
  sleep_score?: number | null;
  sleep_baseline?: number | null;
  sleep_delta_pct?: number | null;
}

// ---- Séance du jour ---------------------------------------------------------

export interface TodayWorkoutResponse {
  rest_day: boolean;
  reason: string | null;
  workout: Workout | null;
  tsb: number | null;
  tsb_zone: string | null;
  rationale?: string | null;
  signals?: Record<string, unknown> | null;
  source?: string | null;
  morning_decision?: string | null;
  morning_reason?: string | null;
  morning_persisted?: boolean;
}

// ---- Auth / comptes (multi-tenant) ------------------------------------------

export interface MeResponse {
  public_id: string;
  role: string;
  display_name: string | null;
}

export interface AcceptInviteResponse {
  session_token: string;
  public_id: string;
  role: string;
}

// ---- Roster coach (liste d'athlètes + invitations) --------------------------

export interface AthleteSummary {
  public_id: string;
  display_name: string | null;
  last_activity_date: string | null;
  n_activities: number;
}

export interface InvitationCreated {
  role: string;
  invite_token: string;
  invite_url: string;
  expires_at: string | null;
}

export interface InvitationOut {
  id: number;
  role: string;
  status: string;
  created_at: string;
  accepted_at: string | null;
}

// ---- Prescription de séances (coach) ----------------------------------------

export type PrescriptionKind = "recovery" | "endurance" | "tempo" | "intervals";

export interface PrescriptionCreate {
  date: string;
  kind: PrescriptionKind;
  duration_min: number;
  notes?: string;
}

export interface PrescriptionOut {
  id: number;
  date: string;
  created_at: string;
  created_by: string | null;
  workout: Workout;
}

export interface ReconnectLink {
  reconnect_url: string;
  expires_at: string | null;
}
