"""Endpoints CRUD du plan d'entraînement + export ZIP de fichiers FIT."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    PlanCreateRequest,
    PlanDetail,
    PlanPushGarminRequest,
    PlanSummary,
    WorkoutSchema,
    WorkoutStepSchema,
)
from domestique_ai.config import get_hr_max, get_hr_rest
from domestique_ai.export.fit import plan_to_zip
from domestique_ai.export.garmin_connect import (
    GarminPushError,
    get_client,
    push_workout,
)
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


def _sse_event(payload: dict[str, Any]) -> dict[str, str]:
    """Construit un événement SSE compatible sse-starlette."""
    return {
        "event": "message",
        "data": json.dumps(payload, ensure_ascii=False),
        "id": payload.get("type", "message"),
    }


async def _push_garmin_stream(
    plan: list[Workout], schedule: bool
) -> AsyncGenerator[dict[str, str], None]:
    """Pipeline SSE pour le push d'un plan vers Garmin Connect.

    Émet successivement :
    - `start` (total)
    - pour chaque séance : `progress` (avant l'upload) puis `result` (après)
    - `done` avec compteurs final (uploaded, errors) ou `error` + `done`

    `push_workout` est synchrone : on l'appelle via `asyncio.to_thread` pour ne
    pas bloquer la boucle ASGI (le réseau Garmin peut prendre plusieurs secondes
    par séance).
    """
    total = len(plan)
    yield _sse_event({"type": "start", "total": total})

    try:
        client = await asyncio.to_thread(get_client)
    except GarminPushError as exc:
        log.warning("Push Garmin : auth échouée — %s", exc)
        yield _sse_event({"type": "error", "value": str(exc)})
        yield _sse_event({"type": "done", "uploaded": 0, "errors": total})
        return

    hr_rest = get_hr_rest()
    hr_max = get_hr_max()
    uploaded = 0
    errors = 0

    for idx, workout in enumerate(plan):
        yield _sse_event(
            {
                "type": "progress",
                "index": idx,
                "total": total,
                "workout": {"date": workout.date, "name": workout.name},
            }
        )
        try:
            workout_id = await asyncio.to_thread(
                push_workout,
                client,
                workout,
                hr_rest=hr_rest,
                hr_max=hr_max,
            )
        except GarminPushError as exc:
            errors += 1
            yield _sse_event(
                {
                    "type": "result",
                    "workout": {"date": workout.date, "name": workout.name},
                    "workout_id": None,
                    "scheduled": False,
                    "error": str(exc),
                }
            )
            continue

        scheduled = False
        scheduling_error: str | None = None
        if schedule:
            try:
                await asyncio.to_thread(
                    client.schedule_workout, workout_id, workout.date
                )
                scheduled = True
            except Exception as exc:  # noqa: BLE001 — on remonte au front
                scheduling_error = str(exc)

        result: dict[str, Any] = {
            "type": "result",
            "workout": {"date": workout.date, "name": workout.name},
            "workout_id": workout_id,
            "scheduled": scheduled,
            "url": f"https://connect.garmin.com/modern/workout/{workout_id}",
        }
        if scheduling_error:
            result["error"] = f"planification : {scheduling_error}"
            errors += 1
        else:
            uploaded += 1
        yield _sse_event(result)

    log.info(
        "Push Garmin terminé : uploaded=%d errors=%d total=%d",
        uploaded,
        errors,
        total,
    )
    yield _sse_event(
        {"type": "done", "uploaded": uploaded, "errors": errors}
    )


@router.post("/{plan_id}/push-garmin")
async def push_plan_garmin(
    plan_id: int, payload: PlanPushGarminRequest
) -> EventSourceResponse:
    """Push un plan vers Garmin Connect en streamant la progression (SSE).

    Le cache token Garmin doit être présent (lancer
    `python -m domestique_ai.export.garmin_connect` une fois pour seeder).
    Si l'auth échoue, on émet un event `error` puis `done` (pas d'exception HTTP
    pour rester compatible avec un client SSE).
    """
    plan = load_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan {plan_id} introuvable.",
        )

    log.info("Push Garmin demandé pour plan %d (schedule=%s)", plan_id, payload.schedule)
    return EventSourceResponse(_push_garmin_stream(plan, payload.schedule))


async def _llm_generation_stream(
    sessions_per_week: int, focus: str | None
) -> AsyncGenerator[dict[str, str], None]:
    """Pipeline SSE pour la génération de plan par LLM, semaine par semaine.

    Events émis :
    - ``start`` {total_weeks, target_date}
    - ``week_completed`` {index, source, workouts: [...], adjustments: [...]}
    - ``done`` {plan_id, total_workouts, llm_weeks, fallback_weeks}
    - ``error`` {value} en cas d'exception fatale (problème de config typiquement)
    """
    try:
        ctx = build_context_from_app_state(
            sessions_per_week=sessions_per_week, focus=focus
        )
    except Exception as exc:  # noqa: BLE001 — on remonte au front
        log.exception("LLM plan : construction du contexte impossible")
        yield _sse_event({"type": "error", "value": str(exc)})
        yield _sse_event({"type": "done", "plan_id": None})
        return

    yield _sse_event(
        {
            "type": "start",
            "target_date": ctx.target_date.isoformat() if ctx.target_date else None,
            "target_event_type": ctx.target_event_type,
            "ctl_current": round(ctx.ctl_current, 1),
        }
    )

    aggregated: list[Workout] = []
    llm_count = 0
    fallback_count = 0

    try:
        async for week in generate_plan_stream(ctx):
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
        target_date=ctx.target_date,
        target_event_type=ctx.target_event_type,
        sessions_per_week=sessions_per_week,
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
async def post_plan_llm(payload: PlanCreateRequest) -> EventSourceResponse:
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
        _llm_generation_stream(payload.sessions_per_week, payload.focus)
    )


@router.get("/{plan_id}/export.ics")
def export_plan_ics(plan_id: int) -> Response:
    """Renvoie le plan au format iCalendar (RFC 5545).

    Le fichier généré s'importe directement dans Google Calendar, Apple
    Calendar et Outlook. Les ``UID`` sont stables (clé ``plan-<id>-<date>``),
    donc réimporter le fichier après modification met à jour les événements
    existants au lieu d'en créer des doublons.
    """
    plan = load_plan(plan_id)
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
