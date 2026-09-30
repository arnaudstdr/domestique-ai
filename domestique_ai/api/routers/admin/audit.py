"""Journal d'audit des actions d'administration (lecture seule)."""

from __future__ import annotations

from fastapi import APIRouter

from domestique_ai import platform_db
from domestique_ai.api.routers.admin._common import AdminAuditEntry

router = APIRouter()


@router.get("/audit", response_model=list[AdminAuditEntry])
def list_audit(limit: int | None = None) -> list[AdminAuditEntry]:
    """Journal d'audit, du plus récent au plus ancien (``limit`` optionnel)."""
    return [AdminAuditEntry(**entry) for entry in platform_db.list_admin_audit(limit=limit)]
