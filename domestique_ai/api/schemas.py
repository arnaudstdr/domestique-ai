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


# ---- Activities --------------------------------------------------------------


class ActivitySummary(BaseModel):
    strava_id: int
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


class ActivityDetail(BaseModel):
    activity: ActivitySummary
    streams: ActivityStreams
    hr_zones: dict[str, float] | None = None


# ---- Morning -----------------------------------------------------------------


class MorningEntry(BaseModel):
    date: str
    hrv_ms: float | None = None
    resting_hr: float | None = None
    sleep_hours: float | None = None
    sleep_score: int | None = None
    stress_score: int | None = None
    notes: str | None = None


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


# ---- Objective ---------------------------------------------------------------


class Objective(BaseModel):
    type: Literal["cyclosportive", "course", "cyclo", "maintenance"] = "maintenance"
    date: str | None = None
    distance_km: float | None = None
    elevation_m: float | None = None
    target_ftp: float | None = None
    target_avg_hr_zone: str | None = None
    notes: str = ""


# ---- Strava sync -------------------------------------------------------------


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


class CoachMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class CoachChatRequest(BaseModel):
    session_id: str | None = None
    message: str
