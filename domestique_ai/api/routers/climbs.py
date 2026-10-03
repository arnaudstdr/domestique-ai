"""Montées détectées : liste, détail, nommage (page « Montées »).

Lecture : tout compte (coach en consultation compris). Écriture (nommage) :
self-only, refusée en consultation coach par ``get_athlete_context``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.schemas import (
    ClimbDetailResponse,
    ClimbRenameRequest,
    ClimbsResponse,
    ClimbSummary,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.processing.climbs import climb_detail, list_climbs, rename_climb

router = APIRouter(prefix="/api/climbs", tags=["climbs"])


@router.get("", response_model=ClimbsResponse)
def get_climbs(
    limit: int = 100,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ClimbsResponse:
    """Montées détectées, les plus grimpées d'abord."""
    segments = list_climbs(limit=limit, ctx=ctx)
    return ClimbsResponse(climbs=[ClimbSummary(**segment) for segment in segments])


@router.get("/{segment_id}", response_model=ClimbDetailResponse)
def get_climb(
    segment_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ClimbDetailResponse:
    """Détail d'une montée : stats + passages."""
    detail = climb_detail(segment_id, ctx=ctx)
    if detail is None:
        raise HTTPException(status_code=404, detail="Montée introuvable.")
    return ClimbDetailResponse(**detail)


@router.put("/{segment_id}", response_model=ClimbDetailResponse)
def update_climb(
    segment_id: int,
    body: ClimbRenameRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ClimbDetailResponse:
    """Nomme (ou renomme) une montée détectée."""
    updated = rename_climb(segment_id, body.name, ctx=ctx)
    if updated is None:
        raise HTTPException(status_code=404, detail="Montée introuvable.")
    return ClimbDetailResponse(**updated)
