"""Journal d'audit des actions d'administration (lecture seule)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Query

from domestique_ai import platform_db
from domestique_ai.api.routers.admin._common import AdminAuditEntry

router = APIRouter()

_PERIODS: dict[str, dt.timedelta | None] = {
    "24h": dt.timedelta(hours=24),
    "7d": dt.timedelta(days=7),
    "30d": dt.timedelta(days=30),
    "all": None,
}


@router.get("/audit", response_model=list[AdminAuditEntry])
def list_audit(
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    action: list[str] | None = Query(None),  # noqa: B008
    q: str | None = None,
    period: Literal["24h", "7d", "30d", "all"] = "all",
) -> list[AdminAuditEntry]:
    """Journal d'audit, du plus récent au plus ancien.

    Filtres : ``action`` (répétable), ``q`` (acteur ou cible : nom, email ou
    id) et ``period`` (fenêtre glissante). Pagination par curseur ``before_id``.
    """
    delta = _PERIODS[period]
    since = (dt.datetime.now(dt.UTC) - delta).isoformat() if delta else None
    return [
        AdminAuditEntry(**entry)
        for entry in platform_db.list_admin_audit(
            limit=limit,
            before_id=before_id,
            actions=action,
            since=since,
            q=q,
        )
    ]
