"""Statistiques plateforme et observabilité ops (scheduler, sync, healthcheck)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from domestique_ai import __version__, platform_db
from domestique_ai.api import scheduler as scheduler_module
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit
from domestique_ai.api.routers.garmin import sync_overview
from domestique_ai.athlete_context import (
    athlete_space_dirs,
    orphan_athlete_space_ids,
    remove_athlete_space,
)
from domestique_ai.config import (
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
    orphan_athlete_spaces: int
    platform_db_bytes: int


class PurgeResult(BaseModel):
    removed: int


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
    known = {u["public_id"] for u in users}
    platform_db_path = get_platform_db_path()
    return AdminStats(
        users_by_role=_counts([u.get("role") for u in users]),
        invitations_by_status=_counts(
            [i.get("status") for i in platform_db.list_invitations(created_by=None)]
        ),
        feedback_by_status=_counts([f.get("status") for f in platform_db.list_feedback()]),
        active_sessions=platform_db.count_active_sessions(),
        garmin_connected=sum(1 for u in users if u.get("has_garmin_credentials")),
        athlete_spaces=len(athlete_space_dirs()),
        orphan_athlete_spaces=len(orphan_athlete_space_ids(known)),
        platform_db_bytes=platform_db_path.stat().st_size if platform_db_path.exists() else 0,
    )


@router.post("/athlete-spaces/purge-orphans", response_model=PurgeResult)
def purge_orphan_athlete_spaces(
    admin: dict = Depends(require_admin),  # noqa: B008
) -> PurgeResult:
    """Supprime les dossiers ``data/athletes/<id>`` sans compte correspondant."""
    known = {u["public_id"] for u in platform_db.list_users()}
    orphans = orphan_athlete_space_ids(known)
    for public_id in orphans:
        remove_athlete_space(public_id)
    audit(
        admin,
        "purge_orphan_spaces",
        details={"removed": len(orphans), "ids": orphans[:100]},
    )
    return PurgeResult(removed=len(orphans))


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
