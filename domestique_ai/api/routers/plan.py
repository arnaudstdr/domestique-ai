"""Endpoints CRUD du plan d'entraînement + export ZIP de fichiers FIT."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    PlanCreateRequest,
    PlanDetail,
    PlanSummary,
    WorkoutSchema,
    WorkoutStepSchema,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.export.fit import plan_to_zip
from domestique_ai.export.ics import plan_to_ics
from domestique_ai.llm.availability import AvailabilityError
from domestique_ai.llm.plan_generator import (
    build_context_from_app_state,
    generate_plan_stream,
)
from domestique_ai.llm.plan_storage import (
    PlanGenerationError,
    build_and_save_plan,
    delete_plan,
    list_plans,
    load_plan,
    save_plan,
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
def get_plans(
    limit: int = 20,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[PlanSummary]:
    """Liste les plans persistés, plus récent d'abord."""
    return [_summary_from_row(row) for row in list_plans(limit=limit, db_path=ctx.db_path)]


@router.post("", response_model=PlanDetail, status_code=status.HTTP_201_CREATED)
def post_plan(
    payload: PlanCreateRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> PlanDetail:
    """Génère un plan d'entraînement et le persiste.

    Lit l'objectif + l'availability + le CTL courant, applique la
    périodisation, sauvegarde, et renvoie le plan complet.
    """
    try:
        plan_id, plan, plan_ctx = build_and_save_plan(
            sessions_per_week=payload.sessions_per_week,
            focus=payload.focus,
            ctx=ctx,
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
        len(plan_ctx["days_used"]),
        plan_ctx["target_date"],
    )

    # On relit la row pour obtenir created_at/weeks calculés par save_plan.
    summary = next(
        (
            _summary_from_row(row)
            for row in list_plans(limit=1, db_path=ctx.db_path)
            if row["id"] == plan_id
        ),
        None,
    )
    return PlanDetail(
        id=plan_id,
        created_at=summary.created_at if summary else "",
        target_date=plan_ctx["target_date"],
        target_event_type=plan_ctx["target_event_type"],
        sessions_per_week=payload.sessions_per_week,
        weeks=summary.weeks if summary else None,
        workouts=[_workout_to_schema(w) for w in plan],
    )


@router.get("/{plan_id}", response_model=PlanDetail)
def get_plan(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> PlanDetail:
    """Charge un plan complet par son id."""
    plan = load_plan(plan_id, db_path=ctx.db_path)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    summary = next(
        (
            _summary_from_row(row)
            for row in list_plans(limit=100, db_path=ctx.db_path)
            if row["id"] == plan_id
        ),
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
def remove_plan(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> None:
    """Supprime un plan persisté."""
    if not delete_plan(plan_id, db_path=ctx.db_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    log.info("Plan %d supprimé", plan_id)


def _sse_event(payload: dict[str, Any]) -> dict[str, str]:
    """Construit un événement SSE compatible sse-starlette."""
    return {
        "event": "message",
        "data": json.dumps(payload, ensure_ascii=False),
        "id": payload.get("type", "message"),
    }


async def _llm_generation_stream(
    sessions_per_week: int, focus: str | None, ctx: AthleteContext
) -> AsyncGenerator[dict[str, str], None]:
    """Pipeline SSE pour la génération de plan par LLM, semaine par semaine.

    Events émis :
    - ``start`` {total_weeks, target_date}
    - ``week_completed`` {index, source, workouts: [...], adjustments: [...]}
    - ``done`` {plan_id, total_workouts, llm_weeks, fallback_weeks}
    - ``error`` {value} en cas d'exception fatale (problème de config typiquement)
    """
    try:
        gen_ctx = build_context_from_app_state(
            sessions_per_week=sessions_per_week, focus=focus, ctx=ctx
        )
    except Exception as exc:  # noqa: BLE001 — on remonte au front
        log.exception("LLM plan : construction du contexte impossible")
        yield _sse_event({"type": "error", "value": str(exc)})
        yield _sse_event({"type": "done", "plan_id": None})
        return

    yield _sse_event(
        {
            "type": "start",
            "target_date": gen_ctx.target_date.isoformat() if gen_ctx.target_date else None,
            "target_event_type": gen_ctx.target_event_type,
            "ctl_current": round(gen_ctx.ctl_current, 1),
        }
    )

    aggregated: list[Workout] = []
    llm_count = 0
    fallback_count = 0

    try:
        async for week in generate_plan_stream(gen_ctx):
            aggregated.extend(week.workouts)
            if week.source == "llm":
                llm_count += 1
            else:
                fallback_count += 1
            yield _sse_event(
                {
                    "type": "week_completed",
                    "index": week.week_index,
                    "source": week.source,
                    "adjustments": week.adjustments,
                    "workouts": [
                        _workout_to_schema(w).model_dump() for w in week.workouts
                    ],
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("LLM plan : exception pendant le streaming")
        yield _sse_event({"type": "error", "value": str(exc)})
        yield _sse_event({"type": "done", "plan_id": None})
        return

    if not aggregated:
        yield _sse_event(
            {
                "type": "error",
                "value": "Aucune séance générée (date cible déjà passée ?).",
            }
        )
        yield _sse_event({"type": "done", "plan_id": None})
        return

    plan_id = save_plan(
        aggregated,
        target_date=gen_ctx.target_date,
        target_event_type=gen_ctx.target_event_type,
        sessions_per_week=sessions_per_week,
        db_path=ctx.db_path,
    )
    log.info(
        "LLM plan %d sauvegardé : %d séances (llm=%d, fallback=%d)",
        plan_id,
        len(aggregated),
        llm_count,
        fallback_count,
    )
    yield _sse_event(
        {
            "type": "done",
            "plan_id": plan_id,
            "total_workouts": len(aggregated),
            "llm_weeks": llm_count,
            "fallback_weeks": fallback_count,
        }
    )


@router.post("/llm")
async def post_plan_llm(
    payload: PlanCreateRequest,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> EventSourceResponse:
    """Génère un plan d'entraînement par LLM, streamé semaine par semaine.

    À chaque semaine, le générateur tente une sortie LLM (avec retry) puis
    bascule sur le builder déterministe si la sortie est invalide. La
    semaine validée est émise via ``week_completed``. À la fin, le plan
    complet est persisté et l'``plan_id`` retourné dans ``done``.
    """
    log.info(
        "LLM plan : génération démarrée (sessions_per_week=%d, focus=%r)",
        payload.sessions_per_week,
        payload.focus,
    )
    return EventSourceResponse(
        _llm_generation_stream(payload.sessions_per_week, payload.focus, ctx)
    )


@router.get("/{plan_id}/export.ics")
def export_plan_ics(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
    """Renvoie le plan au format iCalendar (RFC 5545).

    Le fichier généré s'importe directement dans Google Calendar, Apple
    Calendar et Outlook. Les ``UID`` sont stables (clé ``plan-<id>-<date>``),
    donc réimporter le fichier après modification met à jour les événements
    existants au lieu d'en créer des doublons.
    """
    plan = load_plan(plan_id, db_path=ctx.db_path)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    payload = plan_to_ics(plan, plan_id=plan_id)
    filename = f"plan_{plan[0].date}_{plan[-1].date}.ics"
    return Response(
        content=payload,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{plan_id}/export.zip")
def export_plan_zip(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
    """Renvoie un ZIP des fichiers `.FIT` du plan (un par séance).

    Si l'athlète a un profil HR (repos/max), les fichiers FIT utilisent des
    plages BPM custom (Karvonen). Sinon ils utilisent les zones HR Garmin
    standard configurées sur la montre.
    """
    plan = load_plan(plan_id, db_path=ctx.db_path)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )
    payload = plan_to_zip(plan, hr_rest=ctx.hr_rest, hr_max=ctx.hr_max)
    filename = f"plan_{plan[0].date}_{plan[-1].date}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
