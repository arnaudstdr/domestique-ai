"""Endpoints coach roster — prescription de séances et assignation de plan.

Ces endpoints constituent le **chemin d'écriture maîtrisé** du coach vers l'espace
d'un athlète : ``require_coach`` + vérification d'appartenance au roster. Ils ne
passent **pas** par la garde d'impersonation lecture seule (``?athlete=``) — le
``public_id`` cible est dans le path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from domestique_ai.api.deps import require_coach
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    PlanCreateRequest,
    PlanDetail,
    PrescriptionCreate,
    PrescriptionOut,
    WorkoutSchema,
)
from domestique_ai.athlete_context import AthleteContext, context_for_athlete
from domestique_ai.llm.availability import AvailabilityError
from domestique_ai.llm.plan_storage import (
    PlanGenerationError,
    build_and_save_plan,
    list_plans,
)
from domestique_ai.llm.prescription_storage import (
    PrescriptionError,
    delete_prescription,
    list_prescriptions,
    save_prescription,
)
from domestique_ai.platform_db import get_user_by_public_id, list_athletes_for_coach

router = APIRouter(prefix="/api/roster", tags=["roster"])
log = get_logger("roster")


def _athlete_ctx(public_id: str, coach: dict) -> tuple[dict, AthleteContext]:
    """Résout l'athlète ciblé et vérifie qu'il appartient au roster du coach.

    Lève 403 si l'athlète n'est pas (ou plus) rattaché au coach, ou si le
    ``public_id`` est inconnu — on ne distingue pas les deux pour ne pas fuiter
    l'existence d'un compte.
    """
    target = get_user_by_public_id(public_id)
    roster = {a["public_id"] for a in list_athletes_for_coach(coach["id"])}
    if target is None or public_id not in roster:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Athlète hors de votre roster.",
        )
    return target, context_for_athlete(target)


@router.post(
    "/athletes/{public_id}/prescriptions",
    response_model=PrescriptionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_prescription(
    public_id: str,
    payload: PrescriptionCreate,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> PrescriptionOut:
    _, ctx = _athlete_ctx(public_id, coach)
    try:
        row = save_prescription(
            payload.date,
            payload.kind,
            payload.duration_min,
            payload.notes,
            created_by=coach["public_id"],
            db_path=ctx.db_path,
        )
    except PrescriptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    log.info(
        "Coach %s prescrit %s le %s à l'athlète %s",
        coach["public_id"][:8],
        payload.kind,
        payload.date,
        public_id[:8],
    )
    return PrescriptionOut(**row)


@router.get(
    "/athletes/{public_id}/prescriptions",
    response_model=list[PrescriptionOut],
)
def get_prescriptions(
    public_id: str,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> list[PrescriptionOut]:
    _, ctx = _athlete_ctx(public_id, coach)
    return [PrescriptionOut(**row) for row in list_prescriptions(db_path=ctx.db_path)]


@router.delete(
    "/athletes/{public_id}/prescriptions/{pid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_prescription(
    public_id: str,
    pid: int,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> None:
    _, ctx = _athlete_ctx(public_id, coach)
    if not delete_prescription(pid, db_path=ctx.db_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription {pid} introuvable.",
        )


@router.post(
    "/athletes/{public_id}/plan",
    response_model=PlanDetail,
    status_code=status.HTTP_201_CREATED,
)
def assign_plan(
    public_id: str,
    payload: PlanCreateRequest,
    coach: dict = Depends(require_coach),  # noqa: B008
) -> PlanDetail:
    """Génère et assigne un plan complet (générateur classique) à l'athlète."""
    _, ctx = _athlete_ctx(public_id, coach)
    try:
        plan_id, plan, plan_ctx = build_and_save_plan(
            sessions_per_week=payload.sessions_per_week,
            focus=payload.focus,
            ctx=ctx,
        )
    except PlanGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AvailabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"availability.yaml invalide: {exc}",
        ) from exc

    summary = next(
        (row for row in list_plans(limit=1, db_path=ctx.db_path) if row["id"] == plan_id),
        None,
    )
    log.info(
        "Coach %s assigne le plan %d (%d séances) à l'athlète %s",
        coach["public_id"][:8],
        plan_id,
        len(plan),
        public_id[:8],
    )
    return PlanDetail(
        id=plan_id,
        created_at=summary["created_at"] if summary else "",
        target_date=plan_ctx["target_date"],
        target_event_type=plan_ctx["target_event_type"],
        sessions_per_week=payload.sessions_per_week,
        weeks=summary["weeks"] if summary else None,
        workouts=[WorkoutSchema(**w.to_dict()) for w in plan],
    )
