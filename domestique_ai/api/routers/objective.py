"""Lecture / écriture de l'objectif d'entraînement courant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.schemas import Objective as ObjectiveSchema
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.llm.objectives import (
    Objective as ObjectiveModel,
)
from domestique_ai.llm.objectives import (
    ObjectiveError,
    load_objective,
    save_objective,
)

router = APIRouter(prefix="/api/objective", tags=["objective"])


@router.get("", response_model=ObjectiveSchema | None)
def get_objective(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ObjectiveSchema | None:
    """Renvoie l'objectif persisté ou `null` si absent."""
    obj = load_objective(ctx.objective_path)
    if obj is None:
        return None
    return ObjectiveSchema(
        type=obj.type,  # type: ignore[arg-type]
        date=obj.date,
        distance_km=obj.distance_km,
        elevation_m=obj.elevation_m,
        target_ftp=obj.target_ftp,
        target_avg_hr_zone=obj.target_avg_hr_zone,
        notes=obj.notes or "",
    )


@router.put("", response_model=ObjectiveSchema)
def put_objective(
    payload: ObjectiveSchema,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ObjectiveSchema:
    """Remplace l'objectif courant (réécrit le `objective.yaml` de l'athlète)."""
    try:
        save_objective(
            ObjectiveModel(
                type=payload.type,
                date=payload.date,
                distance_km=payload.distance_km,
                elevation_m=payload.elevation_m,
                target_ftp=payload.target_ftp,
                target_avg_hr_zone=payload.target_avg_hr_zone,
                notes=payload.notes or "",
            ),
            ctx.objective_path,
        )
    except ObjectiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return payload
