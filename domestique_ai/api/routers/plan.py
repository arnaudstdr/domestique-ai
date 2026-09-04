"""Endpoints CRUD du plan d'entraînement + export ZIP de fichiers FIT."""

from __future__ import annotations

import contextlib
import datetime as _dt
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
    PlanDecisionCreate,
    PlanDecisionOut,
    PlanDetail,
    PlanSummary,
    WeeklyReviewOut,
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
    list_decisions,
    list_plans,
    list_versions,
    load_plan,
    save_day_decision,
    save_plan,
)
from domestique_ai.processing.plan_builder import Workout, WorkoutStep

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
        uid=workout.uid,
    )


def _summary_from_row(row: dict) -> PlanSummary:
    return PlanSummary(
        id=row["id"],
        created_at=row["created_at"],
        target_date=row.get("target_date"),
        target_event_type=row.get("target_event_type"),
        sessions_per_week=row.get("sessions_per_week"),
        weeks=row.get("weeks"),
        status=row.get("status", "active"),
        parent_plan_id=row.get("parent_plan_id"),
        start_date=row.get("start_date"),
        adapt_reason=row.get("adapt_reason"),
    )


def _detail_from_plan(plan_id: int, plan: list[Workout], ctx: AthleteContext) -> PlanDetail:
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
        status=summary.status if summary else "active",
        parent_plan_id=summary.parent_plan_id if summary else None,
        start_date=summary.start_date if summary else None,
        adapt_reason=summary.adapt_reason if summary else None,
        workouts=[_workout_to_schema(w) for w in plan],
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
        status=summary.status if summary else "active",
        parent_plan_id=summary.parent_plan_id if summary else None,
        start_date=summary.start_date if summary else None,
        adapt_reason=summary.adapt_reason if summary else None,
        workouts=[_workout_to_schema(w) for w in plan],
    )


@router.get("/active", response_model=PlanDetail)
def get_active_plan(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> PlanDetail:
    """Charge le plan actif (status ``active``), sinon le plus récent."""
    from domestique_ai.llm.plan_storage import load_active_plan

    plan_meta = load_active_plan(db_path=ctx.db_path)
    if plan_meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucun plan actif.",
        )
    plan_id, plan = plan_meta
    return _detail_from_plan(plan_id, plan, ctx)


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
    return _detail_from_plan(plan_id, plan, ctx)


@router.get("/{plan_id}/versions", response_model=list[PlanSummary])
def get_plan_versions(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[PlanSummary]:
    """Ligneage du plan : lui-même + ses ancêtres (via ``parent_plan_id``)."""
    return [_summary_from_row(v) for v in list_versions(plan_id, db_path=ctx.db_path)]


@router.post("/weekly-review", response_model=WeeklyReviewOut)
def post_weekly_review(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> WeeklyReviewOut:
    """Déclenche la revue hebdomadaire : rapport + re-plan adaptatif."""
    from domestique_ai.llm.weekly_review import run_weekly_review

    result = run_weekly_review(ctx=ctx)
    log.info(
        "Revue hebdo (manuel) : decision=%s replanned=%s new_plan_id=%s",
        result.get("decision"),
        result.get("replanned"),
        result.get("new_plan_id"),
    )
    return WeeklyReviewOut(**result)


@router.post("/decision", response_model=PlanDecisionOut)
def post_plan_decision(
    payload: PlanDecisionCreate,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> PlanDecisionOut:
    """Override manuel de la décision du jour (repos complet ou séance allégée).

    La décision est appliquée au plan actif couvrant ``date`` et prime sur le
    plan — le Plan affiche « REPOS (coach) » ou la séance modifiée.
    """
    from domestique_ai.llm.plan_storage import (
        get_day_decision,
        load_active_plan,
    )
    from domestique_ai.llm.today_cache import invalidate as invalidate_today_cache

    plan_meta = load_active_plan(db_path=ctx.db_path)
    if plan_meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucun plan actif pour rattacher la décision.",
        )
    plan_id, workouts = plan_meta
    if not workouts or not (workouts[0].date <= payload.date <= workouts[-1].date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Date {payload.date} hors fenêtre du plan actif.",
        )
    workout = None
    if payload.workout is not None:
        workout = Workout(
            date=payload.workout.date,
            name=payload.workout.name,
            sport=payload.workout.sport,
            kind=payload.workout.kind,
            duration_min=payload.workout.duration_min,
            target_zone=payload.workout.target_zone,
            structure=[
                WorkoutStep(
                    phase=s.phase,  # type: ignore[arg-type]
                    zone=s.zone,
                    duration_sec=s.duration_sec,
                    repeat=s.repeat,
                )
                for s in payload.workout.structure
            ],
            estimated_tss=payload.workout.estimated_tss,
            notes=payload.workout.notes,
        )
    save_day_decision(
        plan_id,
        payload.date,
        payload.decision,
        workout=workout,
        reason=payload.reason,
        decided_by="user",
        db_path=ctx.db_path,
    )
    with contextlib.suppress(Exception):
        invalidate_today_cache(payload.date, db_path=ctx.db_path)
    decision = get_day_decision(plan_id, payload.date, db_path=ctx.db_path)
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Échec de la persistance de la décision.",
        )
    return PlanDecisionOut(
        id=decision["id"],
        plan_id=plan_id,
        date=payload.date,
        decision=decision["decision"],
        workout=_workout_to_schema(decision["workout"]) if decision.get("workout") else None,
        reason=decision.get("reason", ""),
        decided_by=decision.get("decided_by", "user"),
        created_at=decision.get("created_at", ""),
    )


@router.get("/{plan_id}/decisions", response_model=list[PlanDecisionOut])
def get_plan_decisions(
    plan_id: int,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[PlanDecisionOut]:
    """Décisions du check du matin appliquées à un plan."""
    out: list[PlanDecisionOut] = []
    for d in list_decisions(plan_id, db_path=ctx.db_path):
        out.append(
            PlanDecisionOut(
                id=d["id"],
                plan_id=plan_id,
                date=d["date"],
                decision=d["decision"],
                workout=_workout_to_schema(d["workout"]) if d.get("workout") else None,
                reason=d.get("reason", ""),
                decided_by=d.get("decided_by", "daily_check"),
                created_at=d.get("created_at", ""),
            )
        )
    return out


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
                    "workouts": [_workout_to_schema(w).model_dump() for w in week.workouts],
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
        start_date=_dt.date.fromisoformat(aggregated[0].date),
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
