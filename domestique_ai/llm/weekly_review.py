"""
Revue hebdomadaire — le plan s'adapte aux données réelles, semaine après semaine.

Chaque semaine (job scheduler ou déclencheur manuel), le coach :

1. Collecte un rapport de la semaine écoulée : compliance plan vs réalisé
   (``processing/compliance``), tendances matin 14 j (HRV, sommeil, readiness),
   alertes overtraining, TSB/CTL/ATL courants.
2. Arrête un ajustement de volume (``maintain`` / ``reduce`` / ``progress``)
   — règles déterministes d'abord ; le LLM ne fait que rédiger la raison.
3. **Re-compose la semaine à venir** (fenêtre glissante) via le LLM du
   ``plan_generator``, en raisonnant sur l'état réel consolidé
   (``processing.athlete_state``), borné par les garde-fous de
   ``plan_validator``. Le reste du plan actif est conservé et sera réévalué aux
   revues suivantes ; la semaine réduite l'est par le facteur de volume
   déterministe. Le résultat est persisté comme **nouvelle version**
   (``parent_plan_id`` + ``adapt_reason``), l'ancien plan passant en
   ``superseded``. Fallback déterministe si Ollama est injoignable.

Idempotence : un flag ``weekly_review_last_week`` (table ``sync_meta``) évite
de rejouer deux fois la revue la même semaine ISO.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import statistics
from typing import Any

from domestique_ai.athlete_context import AthleteContext, context_from_env
from domestique_ai.config import get_plan_min_ctl
from domestique_ai.ingestion.db import get_sync_meta, set_sync_meta

_WEEKLY_REVIEW_FLAG = "weekly_review_last_week"

# Seuils de décision déterministe.
_MISSED_REDUCE = 2
_ADHERENCE_REDUCE_PCT = 50.0
_READINESS_REDUCE = 50.0
_SLEEP_REDUCE_H = 6.0
_TSB_REDUCE = -15.0
_PROGRESS_ADHERENCE_PCT = 80.0
_VOLUME_REDUCE = 0.85
_VOLUME_PROGRESS = 1.05


def _iso_week_key(date: _dt.date) -> str:
    iso = date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _next_monday(today: _dt.date) -> _dt.date:
    return today + _dt.timedelta(days=7 - today.weekday())


def _previous_week(today: _dt.date) -> tuple[_dt.date, _dt.date]:
    """Bornes lundi-dimanche de la semaine calendaire précédente."""
    this_monday = today - _dt.timedelta(days=today.weekday())
    start = this_monday - _dt.timedelta(days=7)
    return start, start + _dt.timedelta(days=6)


def _median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 1) if clean else None


def collect_week_report(today: _dt.date, ctx: AthleteContext) -> dict[str, Any]:
    """Rapport de la semaine écoulée (best-effort, ne lève pas)."""
    from domestique_ai.llm.plan_storage import list_decisions, load_active_plan
    from domestique_ai.processing.analyzer import (
        calculate_ctl_atl_tsb,
        fetch_activities_from_db,
    )
    from domestique_ai.processing.compliance import compute_week_compliance
    from domestique_ai.processing.morning_metrics import fetch_morning_history
    from domestique_ai.processing.overtraining import detect_overtraining_signals

    report: dict[str, Any] = {
        "date": today.isoformat(),
        "week_key": _iso_week_key(today),
        "active_plan_id": None,
        "compliance": None,
        "morning": {},
        "overtraining": None,
        "tsb": None,
        "ctl": None,
        "signals": {},
    }

    try:
        plan_meta = load_active_plan(ctx.db_path)
        if plan_meta is not None:
            plan_id, workouts = plan_meta
            report["active_plan_id"] = plan_id
            decisions = list_decisions(plan_id, db_path=ctx.db_path)
            activities = fetch_activities_from_db(ctx=ctx)
            week_start, _ = _previous_week(today)
            report["compliance"] = compute_week_compliance(
                workouts, activities, week_start=week_start, decisions=decisions
            )
    except Exception:  # noqa: BLE001
        pass

    try:
        history = fetch_morning_history(days=14, db_path=ctx.db_path)
        last_week = [
            e
            for e in history
            if _previous_week(today)[0].isoformat()
            <= e["date"]
            <= _previous_week(today)[1].isoformat()
        ]
        report["morning"] = {
            "entries_last_week": len(last_week),
            "readiness_median": _median([e.get("readiness_score") for e in last_week]),
            "sleep_median": _median([e.get("sleep_hours") for e in last_week]),
            "hrv_median": _median([e.get("hrv_ms") for e in last_week]),
        }
    except Exception:  # noqa: BLE001
        pass

    with contextlib.suppress(Exception):
        report["overtraining"] = detect_overtraining_signals(ctx=ctx)

    with contextlib.suppress(Exception):
        activities = fetch_activities_from_db(ctx=ctx)
        curves = calculate_ctl_atl_tsb(activities, end_date=today)
        if curves:
            last = curves[-1]
            report["tsb"] = round(float(last.get("TSB", 0.0)), 1)
            report["ctl"] = round(float(last.get("CTL", 0.0)), 1)

    return report


def _fallback_decision(report: dict[str, Any]) -> tuple[str, float, str]:
    """Décision déterministe → ``(action, volume_factor, reason)``."""
    compliance = report.get("compliance") or {}
    missed = compliance.get("missed", 0)
    adherence = compliance.get("adherence_pct", 100.0)
    planned = compliance.get("planned_sessions", 0)
    morning = report.get("morning") or {}
    readiness = morning.get("readiness_median")
    sleep = morning.get("sleep_median")
    tsb = report.get("tsb")

    if planned == 0:
        return (
            "maintain",
            1.0,
            "Aucune séance n'était planifiée la semaine écoulée — plan maintenu.",
        )

    if missed >= _MISSED_REDUCE or adherence < _ADHERENCE_REDUCE_PCT:
        return (
            "reduce",
            _VOLUME_REDUCE,
            f"{missed} séance(s) manquée(s) la semaine écoulée (adhérence {adherence:.0f} %) — "
            "reprise progressive, volume réduit.",
        )
    if readiness is not None and readiness < _READINESS_REDUCE:
        return (
            "reduce",
            _VOLUME_REDUCE,
            f"Readiness médiane basse la semaine écoulée ({readiness:.0f}/100) — volume réduit.",
        )
    if sleep is not None and sleep < _SLEEP_REDUCE_H:
        return (
            "reduce",
            _VOLUME_REDUCE,
            f"Sommeil médian insuffisant ({sleep:.1f} h) la semaine écoulée — volume réduit.",
        )
    if tsb is not None and tsb < _TSB_REDUCE:
        return (
            "reduce",
            _VOLUME_REDUCE,
            f"Fraîcheur dégradée (TSB {tsb:.1f}) — volume réduit pour laisser la récupération.",
        )

    ot = report.get("overtraining") or {}
    for alert in ot.get("alerts", []) or []:
        if alert.get("indicator") in ("tsb_chronic", "strain"):
            return (
                "reduce",
                _VOLUME_REDUCE,
                f"Alerte overtraining ({alert.get('indicator')}) — volume réduit.",
            )

    if missed == 0 and adherence >= _PROGRESS_ADHERENCE_PCT:
        return (
            "progress",
            _VOLUME_PROGRESS,
            f"Semaine conforme (adhérence {adherence:.0f} %) — progression normale.",
        )
    return "maintain", 1.0, "Semaine globalement conforme — plan maintenu."


def _llm_decision(report: dict[str, Any], base: tuple[str, float, str]) -> str:
    """Réécrit la raison par le LLM dans les bornes de la décision (best-effort)."""
    from domestique_ai.llm.ollama_client import chat_structured_sync

    action, _factor, base_reason = base
    schema = {
        "type": "object",
        "properties": {"reason": {"type": "string", "maxLength": 260}},
        "required": ["reason"],
    }
    prompt = (
        "Tu es un coach cyclisme. La revue hebdomadaire a arrêté l'action "
        f"« {action} ». Rédige en français, une à deux phrases, pourquoi et ce "
        "qui change pour l'athlète. Données: compliance="
        f"{report.get('compliance')}, matin={report.get('morning')}, "
        f'TSB={report.get("tsb")}. Retourne un JSON {{"reason": str}}.'
    )
    try:
        result = chat_structured_sync(
            prompt, schema, system="Coach cycliste — bref et concret.", timeout_s=15
        )
        if isinstance(result, dict) and result.get("reason"):
            return str(result["reason"])[:260]
    except Exception:  # noqa: BLE001 — jamais bloquant
        pass
    return base_reason


def _scale_plan(plan: list[Any], factor: float) -> list[Any]:
    """Multiplie les durées des séances (sauf recovery) par ``factor``."""
    from domestique_ai.processing.plan_builder import (
        _TSS_PER_MIN,
        _name_for,
        _structure_for,
    )

    scaled: list[Any] = []
    for w in plan:
        if w.kind == "recovery" or factor >= 1.0:
            scaled.append(w)
            continue
        new_min = max(20, int(w.duration_min * factor))
        if new_min == w.duration_min:
            scaled.append(w)
            continue
        scaled.append(
            type(w)(
                date=w.date,
                name=_name_for(w.kind, new_min, 0, False, False),
                sport=w.sport,
                kind=w.kind,
                duration_min=new_min,
                target_zone=w.target_zone,
                structure=_structure_for(w.kind, new_min),
                estimated_tss=round(_TSS_PER_MIN.get(w.kind, 1.0) * new_min, 1),
                notes=w.notes,
                uid=w.uid,
            )
        )
    return scaled


def run_weekly_review(
    today: _dt.date | None = None,
    *,
    ctx: AthleteContext,
    use_llm: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Exécute la revue hebdomadaire et re-planifie si nécessaire.

    ``force=True`` ignore le flag d'idempotence (revue manuelle / test).
    """
    import asyncio

    from domestique_ai.llm.availability import load_availability
    from domestique_ai.llm.plan_generator import (
        build_context_from_app_state,
        compose_upcoming_week,
    )
    from domestique_ai.llm.plan_storage import get_plan_meta, load_plan, save_plan
    from domestique_ai.llm.today_cache import invalidate as invalidate_today_cache
    from domestique_ai.processing.analyzer import (
        calculate_ctl_atl_tsb,
        fetch_activities_from_db,
    )
    from domestique_ai.processing.athlete_state import build_coach_state
    from domestique_ai.processing.plan_builder import days_used

    ctx = ctx or context_from_env()
    today = today or _dt.date.today()
    week_key = _iso_week_key(today)

    if not force and get_sync_meta(_WEEKLY_REVIEW_FLAG, ctx.db_path) == week_key:
        return {
            "skipped": True,
            "week_key": week_key,
            "reason": "Revue déjà effectuée cette semaine.",
        }

    report = collect_week_report(today, ctx)
    base = _fallback_decision(report)
    reason = _llm_decision(report, base) if use_llm else base[2]
    action, volume_factor, _base_reason = base

    result: dict[str, Any] = {
        "skipped": False,
        "week_key": week_key,
        "report": report,
        "decision": action,
        "volume_factor": volume_factor,
        "reason": reason,
        "replanned": False,
        "new_plan_id": None,
        "parent_plan_id": None,
        "sessions_count": None,
        "adjustments": [],
    }

    parent_id = report.get("active_plan_id")
    if parent_id is None:
        result["reason"] = "Aucun plan actif — pas de re-plan hebdomadaire."
        set_sync_meta(_WEEKLY_REVIEW_FLAG, week_key, ctx.db_path)
        return result

    meta = get_plan_meta(parent_id, ctx.db_path)
    target_date: _dt.date | None = None
    if meta and meta.get("target_date"):
        try:
            target_date = _dt.date.fromisoformat(meta["target_date"])
        except ValueError:
            target_date = None
    target_event_type = (meta.get("target_event_type") if meta else None) or "cyclosportive"
    sessions_per_week = (meta.get("sessions_per_week") if meta else None) or 4
    if target_date is not None and target_date < _next_monday(today):
        result["reason"] = "Objectif atteint ou dépassé — pas de re-plan."
        set_sync_meta(_WEEKLY_REVIEW_FLAG, week_key, ctx.db_path)
        return result

    try:
        activities = fetch_activities_from_db(ctx=ctx)
        curves = calculate_ctl_atl_tsb(activities, end_date=today)
        ctl_current = float(curves[-1]["CTL"]) if curves else 0.0
    except Exception:  # noqa: BLE001
        ctl_current = 0.0

    availability = load_availability(ctx.availability_path)
    min_ctl = get_plan_min_ctl()
    next_monday = _next_monday(today)

    existing_plan = load_plan(parent_id, db_path=ctx.db_path) or []
    week_end = next_monday + _dt.timedelta(days=7)

    # Fenêtre glissante : on ne re-compose QUE la semaine à venir, ancrée sur
    # l'état réel de ce jour (le reste du plan actif est conservé et sera
    # réévalué aux revues suivantes). week_idx repart à 0 → la rampe de reprise
    # et le plafond de progression se mesurent depuis le CTL *actuel*.
    total_weeks = (
        max(1, (target_date - next_monday).days // 7 + 1)
        if target_date is not None
        else 4
    )

    try:
        gen_ctx = build_context_from_app_state(
            sessions_per_week=sessions_per_week, focus=None, today=next_monday, ctx=ctx
        )
        gen_ctx.target_event_type = target_event_type
        gen_ctx.compliance = report.get("compliance")
        gen_ctx.adapt_decision = action
        gen_ctx.adapt_reason = reason
        gen_ctx.coach_state = build_coach_state(
            ctx=ctx,
            today=today,
            activities=activities,
            availability=availability,
            compliance=report.get("compliance"),
            threshold=min_ctl,
        )
    except Exception:  # noqa: BLE001 — contexte d'état réel indisponible
        result["reason"] = "Contexte d'état réel indisponible — plan inchangé."
        set_sync_meta(_WEEKLY_REVIEW_FLAG, week_key, ctx.db_path)
        return result

    try:
        generated = asyncio.run(
            compose_upcoming_week(
                gen_ctx,
                next_monday=next_monday,
                plan_start=next_monday,
                target_date=target_date,
                total_weeks=total_weeks,
                use_llm=use_llm,
            )
        )
    except Exception:  # noqa: BLE001 — jamais bloquant, on garde le plan tel quel
        generated = None

    if generated is None or not generated.workouts:
        result["reason"] = "Semaine à venir inchangée (aucun jour disponible)."
        set_sync_meta(_WEEKLY_REVIEW_FLAG, week_key, ctx.db_path)
        return result

    try:
        # Le facteur de volume déterministe (reduce) borne la semaine composée
        # par le LLM ; progress/maintain laissent le coach libre dans les bornes.
        new_week = _scale_plan(generated.workouts, volume_factor)
        kept = [
            w for w in existing_plan if not (next_monday <= _dt.date.fromisoformat(w.date) < week_end)
        ]
        merged = sorted(kept + list(new_week), key=lambda w: w.date)
        plan_start = min(_dt.date.fromisoformat(w.date) for w in merged)
        new_id = save_plan(
            merged,
            target_date=target_date,
            target_event_type=target_event_type,
            sessions_per_week=sessions_per_week,
            start_date=plan_start,
            parent_plan_id=parent_id,
            adapt_reason=reason,
            db_path=ctx.db_path,
        )
        result.update(
            {
                "replanned": True,
                "new_plan_id": new_id,
                "parent_plan_id": parent_id,
                "sessions_count": len(merged),
                "days_used": days_used(merged),
                "adjustments": generated.adjustments,
                "ctl_current": round(ctl_current, 1),
                "source": generated.source,
            }
        )
        # Le plan a changé : on invalide le cache de la séance du jour.
        with contextlib.suppress(Exception):
            for w in merged:
                invalidate_today_cache(w.date, db_path=ctx.db_path)
    except Exception:  # noqa: BLE001
        result["reason"] = "Échec du re-plan hebdomadaire."
        result["error"] = True
        return result

    set_sync_meta(_WEEKLY_REVIEW_FLAG, week_key, ctx.db_path)
    return result
