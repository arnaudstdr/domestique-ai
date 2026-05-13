"""Endpoints CRUD du plan d'entraînement + export ZIP de fichiers FIT."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    PlanCreateRequest,
    PlanDetail,
    PlanSummary,
    WorkoutSchema,
    WorkoutStepSchema,
)
from domestique_ai.config import get_hr_max, get_hr_rest
from domestique_ai.export.fit import plan_to_zip
from domestique_ai.llm.availability import AvailabilityError
from domestique_ai.llm.plan_storage import (
    PlanGenerationError,
    build_and_save_plan,
    delete_plan,
    list_plans,
    load_plan,
)
from domestique_ai.processing.plan_builder import Workout

router = APIRouter(prefix="/api/plan", tags=["plan"])
log = get_logger("plan")


def _workout_to_schema(workout: Workout) -> WorkoutSchema:
    return WorkoutSchema(
        date=workout.date,
        name=workout.name,
        sport=workout.sport,
        kind=workout.kind,
        duration_min=workout.duration_min,
        target_zone=workout.target_zone,
        structure=[
            WorkoutStepSchema(
                phase=s.phase,  # type: ignore[arg-type]
                zone=s.zone,
                duration_sec=s.duration_sec,
                repeat=s.repeat,
            )
            for s in workout.structure
        ],
        estimated_tss=workout.estimated_tss,
        notes=workout.notes,
    )


def _summary_from_row(row: dict) -> PlanSummary:
    return PlanSummary(
        id=row["id"],
        created_at=row["created_at"],
        target_date=row.get("target_date"),
        target_event_type=row.get("target_event_type"),
        sessions_per_week=row.get("sessions_per_week"),
        weeks=row.get("weeks"),
    )


@router.get("", response_model=list[PlanSummary])
def get_plans(limit: int = 20) -> list[PlanSummary]:
    """Liste les plans persistés, plus récent d'abord."""
    return [_summary_from_row(row) for row in list_plans(limit=limit)]


@router.post("", response_model=PlanDetail, status_code=status.HTTP_201_CREATED)
def post_plan(payload: PlanCreateRequest) -> PlanDetail:
    """Génère un plan d'entraînement et le persiste.

    Lit l'objectif + l'availability + le CTL courant, applique la
    périodisation, sauvegarde, et renvoie le plan complet.
    """
    try:
        plan_id, plan, ctx = build_and_save_plan(
            sessions_per_week=payload.sessions_per_week,
            focus=payload.focus,
        )
    except PlanGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AvailabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"availability.yaml invalide: {exc}",
        ) from exc

    log.info(
        "Plan %d généré : %d séances, %d jours utilisés, target=%s",
        plan_id,
        len(plan),
        len(ctx["days_used"]),
        ctx["target_date"],
    )

    # On relit la row pour obtenir created_at/weeks calculés par save_plan.
    summary = next(
        (_summary_from_row(row) for row in list_plans(limit=1) if row["id"] == plan_id),
        None,
    )
    return PlanDetail(
        id=plan_id,
        created_at=summary.created_at if summary else "",
        target_date=ctx["target_date"],
        target_event_type=ctx["target_event_type"],
        sessions_per_week=payload.sessions_per_week,
        weeks=summary.weeks if summary else None,
        workouts=[_workout_to_schema(w) for w in plan],
    )


@router.get("/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: int) -> PlanDetail:
    """Charge un plan complet par son id."""
    plan = load_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    summary = next(
        (_summary_from_row(row) for row in list_plans(limit=100) if row["id"] == plan_id),
        None,
    )
    return PlanDetail(
        id=plan_id,
        created_at=summary.created_at if summary else "",
        target_date=summary.target_date if summary else None,
        target_event_type=summary.target_event_type if summary else None,
        sessions_per_week=summary.sessions_per_week if summary else None,
        weeks=summary.weeks if summary else None,
        workouts=[_workout_to_schema(w) for w in plan],
    )


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_plan(plan_id: int) -> None:
    """Supprime un plan persisté."""
    if not delete_plan(plan_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    log.info("Plan %d supprimé", plan_id)


@router.get("/{plan_id}/export.zip")
def export_plan_zip(plan_id: int) -> Response:
    """Renvoie un ZIP des fichiers `.FIT` du plan (un par séance).

    Si `STRAVA_HR_REST` et `STRAVA_HR_MAX` sont configurés, les fichiers FIT
    utilisent des plages BPM custom (Karvonen). Sinon ils utilisent les zones
    HR Garmin standard configurées sur la montre.
    """
    plan = load_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    payload = plan_to_zip(plan, hr_rest=get_hr_rest(), hr_max=get_hr_max())
    filename = f"plan_{plan[0].date}_{plan[-1].date}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
