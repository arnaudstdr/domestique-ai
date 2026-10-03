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
  elevation_m: number;
}

export interface RideVolumeResponse {
  year: VolumePeriod;
  week: VolumePeriod;
}

export interface WeeklyVolumeEntry {
  week: string;
  week_starting: string;
  distance_km: number;
  elevation_m: number;
  duration_sec: number;
  sessions: number;
  tss: number;
}

export interface WeeklyVolumeResponse {
  weeks: WeeklyVolumeEntry[];
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
  start_distance_m: number | null;
  track_distance_m: number | null;
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
  has_gps: boolean;
  has_track: boolean;
}

export interface SimilarActivitiesCriteria {
  distance_tolerance_pct: number;
  elevation_tolerance_pct: number;
  sport_bucket: SportBucket;
  start_proximity_m: number | null;
  track_tolerance_m: number | null;
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
  weight_kg: number | null;
  current_wkg: number | null;
  projected_wkg: number | null;
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
  notes: string | null;
  rpe: number | null;
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

export interface ActivityCreate {
  date: string;
  sport_type: string;
  duration_sec: number;
  distance_km: number;
  elevation_m?: number | null;
  avg_hr?: number | null;
  max_hr?: number | null;
  avg_power?: number | null;
  name?: string | null;
  notes?: string | null;
  rpe?: number | null;
}

export interface ActivityUpdate {
  name?: string | null;
  sport_type?: string | null;
  notes?: string | null;
  rpe?: number | null;
}

export interface TcxImportFileResult {
  filename: string;
  status: "imported" | "skipped" | "error";
  reason: string | null;
  activities: ActivitySummary[];
}

export interface TcxImportResponse {
  imported: number;
  skipped: number;
  errors: number;
  results: TcxImportFileResult[];
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
  sleep_stages_json: string | null;
  steps: number | null;
  active_calories: number | null;
  readiness_score: number | null;
  sleep_score_computed: number | null;
  stress_score_computed: number | null;
  weight_kg: number | null;
  source: HealthProvider | null;
  garmin_sleep_score: number | null;
  garmin_readiness_score: number | null;
  garmin_body_battery_min: number | null;
  garmin_body_battery_max: number | null;
}

export interface WeightResponse {
  weight_kg: number | null;
  date: string | null;
  ftp_w: number | null;
  wkg: number | null;
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

export type HealthProvider = "auto" | "garmin" | "google_health";

export interface HealthProviderStatus {
  configured: boolean;
  connected: boolean;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface HealthSourcesResponse {
  provider: HealthProvider;
  provider_effective: "garmin" | "google_health" | null;
  garmin: HealthProviderStatus;
  google_health: HealthProviderStatus;
}

export interface GarminHealthSyncResponse {
  success: boolean;
  synced_dates: string[];
  skipped_dates: string[];
  disabled: boolean;
  message: string;
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
  email: string | null;
  needs_reauth: boolean;
  orphan_tokens: boolean;
  sync: SyncStatus;
}

export interface GarminConnectRequest {
  email: string;
  password: string;
}

export interface GarminMfaRequest {
  code: string;
}

export interface GarminConnectResponse {
  status: "connected" | "mfa_required";
  detail: string | null;
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
  id?: number;
  role: "user" | "assistant";
  content: string;
  thinking: string | null;
  tool_calls: { name: string; arguments: unknown; result: unknown }[] | null;
}

export interface CoachThreadPage {
  messages: CoachMessage[];
  has_more_before: boolean;
  has_more_after: boolean;
}

export interface CoachSearchHit {
  message_id: number | null;
  session_id: string | null;
  source_type: "message" | "summary" | "fact" | string;
  text: string;
  score: number;
}

export type CoachMemoryCategory =
  | "preference"
  | "constraint"
  | "goal"
  | "agreement"
  | "personal";

export interface CoachMemoryFact {
  id: number;
  category: CoachMemoryCategory;
  content: string;
  source_session_id: string | null;
  pinned: boolean;
  active: boolean;
  created_at: string;
  updated_at: string;
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

export interface SubscriptionFeed {
  enabled: boolean;
  url: string;
  webcal_url: string;
  google_url: string;
  qr_svg_data_uri: string;
  athlete_public_id: string;
  reason?: string | null;
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
  level: "beginner" | "intermediate" | "advanced" | "ex_competitor" | "racer";
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

export interface SleepPoint {
  date: string;
  hours: number | null;
}

export interface DailyBriefResponse {
  date: string;
  summary: string;
  coach_tip: string | null;
  tsb: number | null;
  tsb_zone: string | null;
  ctl: number | null;
  atl: number | null;
  primary_alert: DailyBriefAlert | null;
  today_workout: DailyBriefWorkout;
  sleep_history: SleepPoint[];
  week_tss_planned: number | null;
  week_tss_done: number | null;
  week_adherence_pct: number | null;
  week_done: number | null;
  week_partial: number | null;
  week_missed: number | null;
  week_skipped: number | null;
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
  email: string | null;
  totp_enabled: boolean;
  avatar_url: string | null;
  email_verified: boolean;
  has_password: boolean;
  is_bootstrap: boolean;
  terms_accepted_at: string | null;
  terms_accepted_version: string | null;
  health_consent_at: string | null;
  health_consent_version: string | null;
  health_consent_withdrawn_at: string | null;
  onboarding_completed_at: string | null;
  onboarding_dismissed_at: string | null;
}

export interface AuthConfigResponse {
  signup_enabled: boolean;
  legal_version: string;
}

export interface AcceptInviteResponse {
  session_token: string;
  public_id: string;
  role: string;
}

export interface SignupResponse {
  session_token: string;
  public_id: string;
  role: string;
  invite_url: string | null;
  email_verified: boolean;
}

export interface CoachInviteLink {
  invite_url: string;
  coach_code: string;
}

export interface LoginResponse {
  status: "ok" | "totp_required";
  challenge: string | null;
  session_token: string | null;
  public_id: string | null;
  role: string | null;
}

export interface TotpEnrollResponse {
  secret: string;
  otpauth_uri: string;
  qr_svg_data_uri: string;
}

export interface TotpVerifyResponse {
  recovery_codes: string[];
}

export interface StatusResponse {
  status: string;
}

// ---- Roster coach (liste d'athlètes + invitations) --------------------------

export interface AthleteSummary {
  public_id: string;
  display_name: string | null;
  last_activity_date: string | null;
  n_activities: number;
  avatar_url: string | null;
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

// ---- Feedback (retours testeurs) --------------------------------------------

export type FeedbackCategory = "bug" | "idea" | "remark" | "other";

export interface FeedbackPayload {
  category: FeedbackCategory;
  message: string;
  page?: string | null;
  app_version?: string | null;
}

export interface FeedbackCreated {
  id: number;
  created_at: string;
}

// ---- Admin (panneau plateforme, rôle admin uniquement) ----------------------

export type AdminRole = "coach" | "athlete" | "admin";

export type FeedbackStatus = "new" | "acknowledged" | "done" | "rejected";

export interface AdminUser {
  public_id: string;
  role: string;
  display_name: string | null;
  email: string | null;
  email_verified: boolean;
  totp_enabled: boolean;
  has_password: boolean;
  is_bootstrap: boolean;
  created_at: string | null;
  garmin_email: string | null;
  has_garmin_credentials: boolean;
}

export interface AdminFeedback {
  id: number;
  public_id: string | null;
  role: string | null;
  author_email: string | null;
  category: string;
  message: string;
  page: string | null;
  app_version: string | null;
  user_agent: string | null;
  created_at: string;
  status: FeedbackStatus;
}

export interface AdminSettings {
  signup_enabled: boolean;
  maintenance_mode: boolean;
  broadcast_message: string | null;
  llm_price_prompt_per_1k: number;
  llm_price_cached_per_1k: number;
  llm_price_completion_per_1k: number;
  llm_weekly_quota_units: number;
  llm_alert_pct: number;
  llm_model_weights: string;
  llm_model_prices: string;
}

export interface AdminUsageTotals {
  calls: number;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  errors: number;
  avg_duration_ms: number | null;
  estimated_cost_usd: number;
  price_prompt_per_1k: number;
  price_completion_per_1k: number;
}

export interface AdminUsageBreakdown {
  key: string;
  label: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  errors: number;
  estimated_cost_usd: number;
}

export interface AdminUsageActor {
  public_id: string | null;
  display_name: string | null;
  calls: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface AdminUsageCall {
  id: number;
  created_at: string;
  actor_public_id: string | null;
  label: string | null;
  label_human: string;
  entrypoint: string | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_duration_ms: number | null;
  status: string;
  error_type: string | null;
  tools_count: number;
}

export interface AdminOllamaUsage {
  days: number;
  since: string;
  totals: AdminUsageTotals;
  by_label: AdminUsageBreakdown[];
  by_model: AdminUsageBreakdown[];
  by_actor: AdminUsageActor[];
  recent: AdminUsageCall[];
}

export interface AdminCloudModelUsage {
  model: string;
  requests: number;
  weight: number;
  units: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface AdminCloudWindow {
  label: string;
  window_start: string;
  window_end: string;
  seconds_until_reset: number;
  units_used: number;
  requests: number;
  quota_units: number;
  usage_pct: number;
  projected_pct_at_reset: number | null;
}

export interface AdminOllamaCloud {
  period: string;
  period_start: string;
  period_end: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  alert_pct: number;
  recommendation: string;
  session: AdminCloudWindow;
  weekly: AdminCloudWindow;
  models: AdminCloudModelUsage[];
}

export interface Announcement {
  maintenance_mode: boolean;
  message: string | null;
}

export interface AdminLink {
  public_id: string;
  display_name: string | null;
  email: string | null;
}

export interface AdminUserDetail extends AdminUser {
  locked: boolean;
  failed_attempts: number;
  locked_until: string | null;
  password_changed_at: string | null;
  last_activity_date: string | null;
  n_activities: number;
  coaches: AdminLink[];
  athletes_count: number;
}

export interface AdminSession {
  id: number;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface AdminAuditEntry {
  id: number;
  actor_public_id: string | null;
  actor_label: string | null;
  action: string;
  target_public_id: string | null;
  target_label: string | null;
  details: unknown;
  created_at: string;
}

export interface AdminAuditQuery {
  limit?: number;
  beforeId?: number;
  actions?: string[];
  q?: string;
  period?: "24h" | "7d" | "30d" | "all";
}

export interface AdminInvitation {
  id: number;
  role: string;
  status: string;
  created_at: string | null;
  expires_at: string | null;
  accepted_at: string | null;
  created_by_public_id: string | null;
  created_by_email: string | null;
  accepted_public_id: string | null;
}

export interface AdminInvitationCreated {
  invitation: AdminInvitation;
  invite_url: string;
}

export interface AdminStats {
  users_by_role: Record<string, number>;
  invitations_by_status: Record<string, number>;
  feedback_by_status: Record<string, number>;
  active_sessions: number;
  garmin_connected: number;
  athlete_spaces: number;
  orphan_athlete_spaces: number;
  platform_db_bytes: number;
}

export interface SchedulerJob {
  id: string;
  next_run_time: string | null;
}

export interface AdminStatus {
  version: string;
  scheduler_running: boolean;
  jobs: SchedulerJob[];
  healthcheck_configured: boolean;
  healthcheck_last: { ok: boolean; at: string } | null;
  garmin_syncing: number;
  garmin_errors: number;
  garmin_last_finished_at: string | null;
  timezone: string;
  daily_check: string | null;
  weekly_review: string | null;
}
