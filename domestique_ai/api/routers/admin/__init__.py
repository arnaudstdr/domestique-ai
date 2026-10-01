"""Panneau d'administration plateforme (réservé au rôle ``admin``).

L'admin est volontairement **isolé** : il n'accède qu'à ``/api/admin/*`` (gestion
des comptes, retours cross-tenant, réglages plateforme, journal d'audit) et
n'hérite pas des droits coach. Le rôle ``admin`` n'est jamais auto-attribuable :
il se crée/promeut hors-ligne (``python -m domestique_ai.auth_cli …``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from domestique_ai.api.deps import require_admin

from . import audit, feedback, invitations, llm_usage, platform, settings, users

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

router.include_router(users.router)
router.include_router(feedback.router)
router.include_router(audit.router)
router.include_router(invitations.router)
router.include_router(platform.router)
router.include_router(settings.router)
router.include_router(llm_usage.router)

__all__ = ["router"]
