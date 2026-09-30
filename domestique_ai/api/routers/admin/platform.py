"""Statistiques plateforme et observabilité ops (scheduler, sync, healthcheck)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from domestique_ai import __version__, platform_db
from domestique_ai.api import scheduler as scheduler_module
from domestique_ai.api.routers.garmin import sync_overview
from domestique_ai.config import (
    get_athletes_root,
    get_daily_check_time,
    get_platform_db_path,
    get_scheduler_timezone,
    get_weekly_review_time,
)
from domestique_ai.healthcheck import configured as healthcheck_configured
from domestique_ai.healthcheck import last_ping as healthcheck_last_ping

router = APIRouter()


class AdminStats(BaseModel):
    users_by_role: dict[str, int]
    invitations_by_status: dict[str, int]
    feedback_by_status: dict[str, int]
    active_sessions: int
    garmin_connected: int
    athlete_spaces: int
    platform_db_bytes: int


class SchedulerJob(BaseModel):
    id: str
    next_run_time: str | None = None


class AdminStatus(BaseModel):
    version: str
    scheduler_running: bool
    jobs: list[SchedulerJob]
    healthcheck_configured: bool
    healthcheck_last: dict[str, Any] | None = None
    garmin_syncing: int
    garmin_errors: int
    garmin_last_finished_at: str | None = None
    timezone: str
    daily_check: str | None = None
    weekly_review: str | None = None


def _counts(values: list[str | None]) -> dict[str, int]:
    return dict(Counter(v or "—" for v in values))


@router.get("/stats", response_model=AdminStats)
def get_stats() -> AdminStats:
    """Statistiques agrégées de la plateforme."""
    users = platform_db.list_users()
    root = get_athletes_root()
    athlete_spaces = sum(1 for p in root.iterdir() if p.is_dir()) if root.exists() else 0
    platform_db_path = get_platform_db_path()
    return AdminStats(
        users_by_role=_counts([u.get("role") for u in users]),
        invitations_by_status=_counts(
            [i.get("status") for i in platform_db.list_invitations(created_by=None)]
        ),
        feedback_by_status=_counts([f.get("status") for f in platform_db.list_feedback()]),
        active_sessions=platform_db.count_active_sessions(),
        garmin_connected=sum(1 for u in users if u.get("has_garmin_credentials")),
        athlete_spaces=athlete_spaces,
        platform_db_bytes=platform_db_path.stat().st_size if platform_db_path.exists() else 0,
    )


@router.get("/status", response_model=AdminStatus)
def get_status() -> AdminStatus:
    """Santé opérationnelle : version, scheduler, sync Garmin, healthcheck."""
    scheduler = scheduler_module.jobs_snapshot()
    garmin = sync_overview()
    daily = get_daily_check_time()
    weekly = get_weekly_review_time()
    return AdminStatus(
        version=__version__,
        scheduler_running=bool(scheduler["running"]),
        jobs=[SchedulerJob(**j) for j in scheduler["jobs"]],  # type: ignore[arg-type]
        healthcheck_configured=healthcheck_configured(),
        healthcheck_last=healthcheck_last_ping(),
        garmin_syncing=int(garmin["syncing"]),
        garmin_errors=int(garmin["errors"]),
        garmin_last_finished_at=garmin["last_finished_at"],
        timezone=get_scheduler_timezone(),
        daily_check=f"{daily[0]:02d}:{daily[1]:02d}" if daily else None,
        weekly_review=f"{weekly[0]}/{weekly[1]:02d}:00" if weekly else None,
    )
