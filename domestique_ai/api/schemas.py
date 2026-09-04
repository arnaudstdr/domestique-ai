"""Schémas Pydantic v2 exposés par l'API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- Metrics -----------------------------------------------------------------


class LoadPoint(BaseModel):
    """Point d'une courbe CTL/ATL/TSB."""

    date: str
    ctl: float
    atl: float
    tsb: float


class LoadCurrent(BaseModel):
    """État courant de la charge avec zone interprétative."""

    ctl: float
    atl: float
    tsb: float
    zone: Literal["freshness", "optimal", "overreaching", "overtraining"]
    zone_label_fr: str


class LoadResponse(BaseModel):
    current: LoadCurrent | None
    history: list[LoadPoint]


class OvertrainingIndicators(BaseModel):
    chronic_tsb: float | None = None
    monotony: float | None = None
    strain: float | None = None
    weekly_jump_pct: float | None = None


class Alert(BaseModel):
    type: str
    level: Literal["warning", "danger"]
    message: str


class OvertrainingResponse(BaseModel):
    alerts: list[Alert]
    indicators: OvertrainingIndicators


class VolumePeriod(BaseModel):
    distance_km: float
    duration_sec: int


class RideVolumeResponse(BaseModel):
    year: VolumePeriod
    week: VolumePeriod


# ---- Tendances longues -------------------------------------------------------


class TrendLoadPoint(BaseModel):
    """Point de la courbe CTL/ATL/TSB sous-échantillonnée selon la résolution."""

    date: str
    ctl: float
    atl: float
    tsb: float


class TrendMonthlyEntry(BaseModel):
    """Agrégat mensuel + valeurs N-1 alignées sur le même mois."""

    month: str  # ``"YYYY-MM"``
    distance_km: float
    elevation_m: float
    duration_sec: int
    sessions: int
    tss: float
    distance_km_n1: float | None = None
    tss_n1: float | None = None
    z1_pct: float | None = None
    z2_pct: float | None = None
    z3_pct: float | None = None
    z4_pct: float | None = None
    z5_pct: float | None = None


class TrendsResponse(BaseModel):
    """Réponse de ``GET /api/metrics/trends``."""

    period: Literal["3m", "6m", "1y", "all"]
    resolution: Literal["day", "week", "month"]
    load_history: list[TrendLoadPoint]
    monthly: list[TrendMonthlyEntry]


class FtpProjectionResponse(BaseModel):
    """Réponse de ``GET /api/metrics/ftp-projection``."""

    current_ftp: float | None
    projected_ftp: float | None
    delta_pct: float
    delta_ctl_28d: float | None
    ctl_current: float | None
    z4_z5_share_pct: float | None
    confidence: Literal["low", "medium", "high"]
    history_days: int


# ---- Activities --------------------------------------------------------------


class ActivitySummary(BaseModel):
    external_id: int
    name: str | None = None
    date: str
    distance_km: float
    duration_sec: int
    elevation_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power: float | None = None
    tss: float
    sport_type: str | None = None
    hr_zones_sec: dict[str, float | None] | None = None
    avg_temp: float | None = None
    min_temp: float | None = None
    max_temp: float | None = None
    map_polyline: str | None = None
    # Champs enrichis (payload liste Garmin, 09/2026) — NULL selon device/sport.
    calories: float | None = None
    max_power: float | None = None
    cadence_avg: float | None = None
    cadence_max: float | None = None
    # Vitesses stockées en m/s en DB, exposées en km/h (arrondies 0.1).
    speed_avg_kmh: float | None = None
    speed_max_kmh: float | None = None
    elevation_loss: float | None = None
    # Source d'ingestion : "strava" (lignes historiques, ingestion supprimée
    # en 09/2026) ou "garmin". Le champ ``external_id`` porte l'id externe de
    # la source (strava_id legacy ou garmin_id).
    source: str = "strava"


class ActivitiesList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ActivitySummary]


class ActivityStreams(BaseModel):
    time: list[int] | None = None
    heartrate: list[float] | None = None
    altitude: list[float] | None = None
    watts: list[float] | None = None
    latlng: list[list[float]] | None = None
    cadence: list[float] | None = None
    velocity_smooth: list[float] | None = None
    distance: list[float] | None = None
    temp: list[float] | None = None


class ActivityDetail(BaseModel):
    activity: ActivitySummary
    streams: ActivityStreams
    hr_zones: dict[str, float] | None = None


class ActivityWeather(BaseModel):
    """Météo au lieu/heure d'une activité (endpoint Garmin ``/weather``).

    ``available`` à ``False`` quand Garmin ne renvoie rien (activité indoor,
    endpoint injoignable) — le front masque alors la carte.
    """

    available: bool = False
    issue_date: str | None = None
    temp_c: float | None = None
    apparent_temp_c: float | None = None
    dew_point_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_direction_deg: float | None = None
    wind_compass: str | None = None
    description: str | None = None
    station: str | None = None


class SimilarActivityMatch(BaseModel):
    """Une activité similaire à l'activité de référence."""

    external_id: int
    date: str
    duration_sec: int | None = None
    avg_heart_rate: float | None = None
    avg_power: float | None = None
    elevation_m: float
    distance_km: float
    training_load: float | None = None
    duration_delta_pct: float | None = None
    tss_delta_pct: float | None = None
    power_delta_pct: float | None = None


class SimilarActivitiesReference(BaseModel):
    external_id: int
    date: str
    distance_km: float
    elevation_m: float
    duration_sec: int | None = None
    training_load: float | None = None
    sport_bucket: Literal["indoor", "outdoor", "other"]


class SimilarActivitiesCriteria(BaseModel):
    distance_tolerance_pct: float
    elevation_tolerance_pct: float
    sport_bucket: Literal["indoor", "outdoor", "other"]


class SimilarActivitiesResponse(BaseModel):
    """Réponse de ``GET /api/activities/{external_id}/similar``.

    Si l'activité n'est pas exploitable (introuvable, trop courte), on renvoie
    ``available=False`` + ``reason``. Sinon ``matches`` peut être vide quand
    aucune activité ne tombe dans les tolérances.
    """

    available: bool
    reason: str | None = None
    reference: SimilarActivitiesReference | None = None
    matches: list[SimilarActivityMatch] = Field(default_factory=list)
    criteria: SimilarActivitiesCriteria | None = None


# ---- Morning -----------------------------------------------------------------


class MorningEntry(BaseModel):
    date: str
    hrv_ms: float | None = None
    resting_hr: float | None = None
    sleep_hours: float | None = None
    sleep_score: int | None = None
    stress_score: int | None = None
    notes: str | None = None
    spo2_avg_pct: float | None = None
    respiratory_rate_avg_bpm: float | None = None
    skin_temp_delta_c: float | None = None
    sleep_deep_min: int | None = None
    sleep_rem_min: int | None = None
    sleep_light_min: int | None = None
    sleep_awake_min: int | None = None
    steps: int | None = None
    active_calories: int | None = None
    readiness_score: int | None = None
    sleep_score_computed: int | None = None


class MorningBaseline(BaseModel):
    available: bool
    metric: str
    baseline: float | None = None
    latest: float | None = None
    latest_date: str | None = None
    delta_pct: float | None = None
    sample_size: int | None = None
    reason: str | None = None


class MorningAlert(BaseModel):
    metric: str
    delta_pct: float
    baseline: float
    latest: float
    latest_date: str
    severity: Literal["warning", "critical"]


class MorningResponse(BaseModel):
    history: list[MorningEntry]
    baselines: dict[str, MorningBaseline]
    alerts: list[MorningAlert]


class MorningSubmit(BaseModel):
    date: str | None = Field(
        default=None,
        description="Date ISO YYYY-MM-DD. Défaut: today.",
    )
    hrv_ms: float | None = None
    resting_hr: float | None = None
    sleep_hours: float | None = None
    sleep_score: int | None = None
    stress_score: int | None = None
    notes: str | None = None
    spo2_avg_pct: float | None = None
    respiratory_rate_avg_bpm: float | None = None
    skin_temp_delta_c: float | None = None
    sleep_deep_min: int | None = None
    sleep_rem_min: int | None = None
    sleep_light_min: int | None = None
    sleep_awake_min: int | None = None
    steps: int | None = None
    active_calories: int | None = None
    readiness_score: int | None = None


# ---- Google Health ---------------------------------------------------------


class GoogleHealthStatusResponse(BaseModel):
    configured: bool
    authenticated: bool
    last_sync_at: str | None = None


class GoogleHealthSyncResponse(BaseModel):
    success: bool
    synced_dates: list[str]
    skipped_dates: list[str]
    message: str


class GoogleHealthAuthResponse(BaseModel):
    auth_url: str


# ---- Objective ---------------------------------------------------------------


class Objective(BaseModel):
    type: Literal["cyclosportive", "course", "cyclo", "forme", "maintenance"] = "maintenance"
    date: str | None = None
    distance_km: float | None = None
    elevation_m: float | None = None
    target_ftp: float | None = None
    target_avg_hr_zone: str | None = None
    notes: str = ""


# ---- Profil utilisateur ------------------------------------------------------


class ProfileSchema(BaseModel):
    """Paramètres physiologiques persistés dans ``data/profile.yaml``."""

    ftp: float | None = Field(default=None, gt=0)
    hr_rest: float | None = Field(default=None, gt=0)
    hr_max: float | None = Field(default=None, gt=0)
    sex: Literal["M", "F"] = "M"
    lthr_pct: float = Field(default=0.88, ge=0.5, le=1.0)


# ---- Disponibilité hebdomadaire ----------------------------------------------


class DayAvailabilityIn(BaseModel):
    max_duration_min: int = Field(ge=20)
    context: Literal["indoor", "outdoor"]


class AvailabilityPreferencesSchema(BaseModel):
    long_endurance_day: str | None = None  # nom anglais lowercase
    intervals_day: str | None = None


class AvailabilitySchema(BaseModel):
    days: dict[str, DayAvailabilityIn]
    preferences: AvailabilityPreferencesSchema | None = None


# ---- Sync --------------------------------------------------------------------


class SyncStatus(BaseModel):
    status: Literal["idle", "syncing", "done", "error"]
    inserted: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class SyncResult(BaseModel):
    status: str
    updated: int | None = None
    inserted: int | None = None
    error: str | None = None


# ---- Coach -------------------------------------------------------------------


class CoachSession(BaseModel):
    session_id: str
    started_at: str
    messages: int
    preview: str
    title: str | None = None


class CoachMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class CoachChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class CoachAnalyzeRequest(BaseModel):
    prompt: str


# ---- Plan d'entraînement -----------------------------------------------------


class WorkoutStepSchema(BaseModel):
    phase: Literal["warmup", "active", "rest", "cooldown"]
    zone: str
    duration_sec: int
    repeat: int = 1


class WorkoutSchema(BaseModel):
    date: str
    name: str
    sport: str = "cycling"
    kind: str
    duration_min: int
    target_zone: str
    structure: list[WorkoutStepSchema] = Field(default_factory=list)
    estimated_tss: float = 0.0
    notes: str = ""
    uid: str = ""


class PlanSummary(BaseModel):
    """Vue compacte d'un plan persisté (liste des plans)."""

    id: int
    created_at: str
    target_date: str | None = None
    target_event_type: str | None = None
    sessions_per_week: int | None = None
    weeks: int | None = None
    status: str = "active"
    parent_plan_id: int | None = None
    start_date: str | None = None
    adapt_reason: str | None = None


class PlanDetail(BaseModel):
    """Plan complet avec toutes ses séances."""

    id: int
    created_at: str
    target_date: str | None = None
    target_event_type: str | None = None
    sessions_per_week: int | None = None
    weeks: int | None = None
    status: str = "active"
    parent_plan_id: int | None = None
    start_date: str | None = None
    adapt_reason: str | None = None
    workouts: list[WorkoutSchema]


class PlanCreateRequest(BaseModel):
    sessions_per_week: int = Field(default=4, ge=2, le=7)
    focus: str | None = None


class PlanDecisionCreate(BaseModel):
    """Override manuel de la décision du jour (repos / séance allégée)."""

    date: str
    decision: Literal["rest", "adjusted"]
    reason: str = ""
    workout: WorkoutSchema | None = None


class PlanDecisionOut(BaseModel):
    """Décision du check du matin appliquée à un jour du plan."""

    id: int
    plan_id: int
    date: str
    decision: str
    workout: WorkoutSchema | None = None
    reason: str = ""
    decided_by: str = "daily_check"
    created_at: str


class WeeklyReviewOut(BaseModel):
    """Résultat d'une revue hebdomadaire (re-plan adaptatif)."""

    skipped: bool = False
    week_key: str | None = None
    decision: str = "maintain"
    volume_factor: float = 1.0
    reason: str = ""
    replanned: bool = False
    new_plan_id: int | None = None
    parent_plan_id: int | None = None
    sessions_count: int | None = None
    error: bool = False
    report: dict[str, Any] = Field(default_factory=dict)


class PrescriptionCreate(BaseModel):
    date: str
    kind: Literal["recovery", "endurance", "tempo", "intervals"]
    duration_min: int = Field(ge=20, le=600)
    notes: str = ""


class PrescriptionOut(BaseModel):
    id: int
    date: str
    created_at: str
    created_by: str | None = None
    workout: WorkoutSchema


class ReconnectLink(BaseModel):
    reconnect_url: str
    expires_at: str | None = None


class PlanPushGarminRequest(BaseModel):
    schedule: bool = True


# ---- Séance du jour ---------------------------------------------------------


class DailyBriefAlert(BaseModel):
    """Alerte la plus saillante du jour (overtraining ou dérive matinale)."""

    type: str
    severity: Literal["warning", "danger"]
    message: str


class DailyBriefWorkout(BaseModel):
    """Vue compacte de la séance du jour pour le brief Dashboard.

    Inclut la structure détaillée pour permettre au composant frontal de
    déplier les steps sans appel HTTP supplémentaire.
    """

    rest_day: bool
    reason: str | None = None
    kind: str | None = None
    duration_min: int | None = None
    name: str | None = None
    target_zone: str | None = None
    estimated_tss: float | None = None
    structure: list[WorkoutStepSchema] = Field(default_factory=list)
    notes: str | None = None


class DailyBriefResponse(BaseModel):
    """Réponse de ``GET /api/coach/daily-brief`` — palier 1 de la proactivité."""

    date: str
    summary: str
    tsb: float | None = None
    tsb_zone: str | None = None
    primary_alert: DailyBriefAlert | None = None
    today_workout: DailyBriefWorkout
    source: Literal["cache", "llm", "fallback"]
    # Check du matin : décision go / adjust / rest répercutée dans le plan.
    morning_decision: str | None = None
    morning_reason: str | None = None
    morning_persisted: bool = False
    # Sommeil de la dernière nuit (coaching ciblé).
    sleep_hours: float | None = None
    sleep_score: int | None = None
    sleep_baseline: float | None = None
    sleep_delta_pct: float | None = None


class TodayWorkoutResponse(BaseModel):
    """Réponse de ``GET /api/coach/today``.

    Soit ``rest_day=True`` (jour off selon la disponibilité), soit
    ``workout`` rempli avec une séance structurée et le contexte TSB.
    Inclut un ``rationale`` (justification courte) et ``signals`` (données
    contextuelles ayant abouti à la décision) quand disponibles.
    """

    rest_day: bool = False
    reason: str | None = None
    workout: WorkoutSchema | None = None
    tsb: float | None = None
    tsb_zone: str | None = None
    rationale: str | None = None
    signals: dict[str, Any] | None = None
    source: Literal["cache", "llm", "fallback", "plan"] | None = None
    # Check du matin : décision go / adjust / rest répercutée dans le plan.
    morning_decision: str | None = None
    morning_reason: str | None = None
    morning_persisted: bool = False
