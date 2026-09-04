"""
Génération d'un plan d'entraînement par LLM, sécurisée par les garde-fous
déterministes de ``processing.plan_validator``.

Le LLM produit uniquement les choix de haut niveau (``kind``, ``duration_min``,
``notes``) pour chaque jour disponible d'une semaine. Le module reconstruit
ensuite ``structure``, ``target_zone`` et ``estimated_tss`` à partir des
helpers du builder déterministe — ce qui évite que le modèle invente des
schémas physiologiquement absurdes (20 min de Z5 d'affilée, etc.).

Flow :
1. Pour chaque semaine du plan, on appelle ``chat_structured`` avec un prompt
   strict (système + données chiffrées + contraintes).
2. La sortie JSON est validée contre ``WeekPlan`` (Pydantic v2).
3. Une retry est effectuée si la 1re sortie est invalide.
4. Si la 2e tentative échoue, on retombe sur le builder déterministe pour
   *cette semaine* uniquement (les autres semaines restent côté LLM).
5. Le plan complet est rejoué par ``validate_and_correct`` avant persistance.

L'endpoint SSE émet des événements semaine par semaine pour montrer la
progression côté UI.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import statistics
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from domestique_ai.athlete_context import AthleteContext
from domestique_ai.llm.availability import _WEEKDAY_BY_INDEX, Availability
from domestique_ai.llm.ollama_client import chat_structured
from domestique_ai.processing.plan_builder import (
    _BASE_DURATION_MIN,
    _TARGET_ZONE,
    _TSS_PER_MIN,
    Workout,
    _name_for,
    _objective_flavor,
    _structure_for,
    _training_emphasis,
    build_training_plan,
)
from domestique_ai.processing.plan_validator import validate_and_correct

_VALID_KINDS = ("recovery", "endurance", "tempo", "intervals")
_GENERATION_TIMEOUT_S = 30.0


class LLMWorkoutDraft(BaseModel):
    """Schéma strict de la sortie LLM pour une séance (1 jour)."""

    date: str = Field(description="Date ISO YYYY-MM-DD")
    kind: Literal["recovery", "endurance", "tempo", "intervals"]
    duration_min: int = Field(ge=20, le=300)
    notes: str = Field(default="", max_length=500)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        try:
            _dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"date ISO invalide: {value!r}") from exc
        return value


class LLMWeekPlan(BaseModel):
    """Schéma strict d'une semaine complète."""

    workouts: list[LLMWorkoutDraft] = Field(default_factory=list, max_length=7)


@dataclass
class GeneratedWeek:
    """Résultat d'une génération de semaine, avec métadonnées de provenance."""

    week_index: int
    workouts: list[Workout]
    source: Literal["llm", "fallback"]
    adjustments: list[str]


def _expand_to_workout(draft: LLMWorkoutDraft, week_index: int, focus: str | None) -> Workout:
    """Construit un ``Workout`` complet à partir des choix de haut niveau du LLM."""
    duration_min = max(20, int(draft.duration_min))
    return Workout(
        date=draft.date,
        name=_name_for(draft.kind, duration_min, week_index, False, False),
        sport="cycling",
        kind=draft.kind,
        duration_min=duration_min,
        target_zone=_TARGET_ZONE[draft.kind],
        structure=_structure_for(draft.kind, duration_min),
        estimated_tss=round(_TSS_PER_MIN[draft.kind] * duration_min, 1),
        notes=draft.notes or (focus or ""),
    )


def _week_dates(
    week_start: _dt.date,
    availability: Availability | None,
) -> list[_dt.date]:
    """Liste des dates disponibles dans la semaine ``week_start`` (lundi)."""
    if availability is None:
        weekdays = [0, 1, 2, 3, 4, 5, 6]
    else:
        weekdays = sorted(d.weekday for d in availability.days)
    return [week_start + _dt.timedelta(days=wd) for wd in weekdays]


def _build_system_prompt() -> str:
    """Prompt système pour la génération d'une semaine.

    Volontairement court et strict : on précise le contrat JSON, les types de
    séance autorisés, et on rappelle les bornes de durée.
    """
    return (
        "Tu es un coach d'endurance qui conçoit des semaines d'entraînement "
        "pour un cycliste. Ta sortie est un JSON strict qui respecte ce "
        "schéma :\n"
        "{\n"
        '  "workouts": [\n'
        '    {"date": "YYYY-MM-DD", "kind": "...", '
        '"duration_min": int, "notes": "..."}\n'
        "  ]\n"
        "}\n\n"
        f"Champs autorisés pour kind : {', '.join(_VALID_KINDS)}.\n"
        "duration_min : entre 20 et 300 minutes.\n"
        "Tu génères UNE séance par date demandée, dans l'ordre chronologique. "
        "Pas de texte hors JSON, pas de commentaires Markdown. "
        "Les notes restent courtes (1 phrase max)."
    )


def _build_user_prompt(
    week_index: int,
    total_weeks: int,
    dates: list[_dt.date],
    objective_type: str,
    weeks_to_event: int | None,
    ctl_current: float,
    focus: str | None,
    is_taper: bool,
    is_recovery_week: bool,
    availability: Availability | None,
    adaptation: str = "",
    emphasis: str = "",
) -> str:
    """Prompt utilisateur : contexte chiffré + contraintes hebdo + intentions."""
    lines: list[str] = []
    lines.append(
        f"Semaine {week_index + 1} sur {total_weeks}. "
        f"Génère {len(dates)} séances pour les dates : "
        f"{', '.join(d.isoformat() for d in dates)}."
    )
    if weeks_to_event is not None:
        lines.append(f"L'objectif ({objective_type}) est dans {weeks_to_event} semaines.")
    else:
        lines.append(f"Pas d'objectif daté — mode {objective_type}.")
    lines.append(f"CTL courant : {ctl_current:.1f} pts.")

    phase = (
        "TAPER (volume réduit, intensité maintenue, fraîcheur)"
        if is_taper
        else "RÉCUPÉRATION (volume −35 %, pas d'intervalles)"
        if is_recovery_week
        else "CHARGE (progression progressive du volume)"
    )
    lines.append(f"Phase : {phase}.")

    if adaptation:
        lines.append("État réel de l'athlète (données calculées, fiables) :")
        lines.append(adaptation)

    if emphasis:
        lines.append(emphasis)

    if focus:
        lines.append(f"Focus spécifique demandé : {focus}.")

    if availability is not None:
        constraints: list[str] = []
        for date in dates:
            day = availability.get(date.weekday())
            if day is not None:
                constraints.append(
                    f"- {date.isoformat()} ({day.name}, {day.context}) : "
                    f"max {day.max_duration_min} min"
                )
        if constraints:
            lines.append("Contraintes par jour :")
            lines.extend(constraints)
        prefs: list[str] = []
        if availability.intervals_day is not None:
            prefs.append(
                f"jour intervalles = {_WEEKDAY_BY_INDEX[availability.intervals_day]}"
            )
        if availability.long_endurance_day is not None:
            prefs.append(
                f"jour sortie longue = {_WEEKDAY_BY_INDEX[availability.long_endurance_day]}"
            )
        if prefs:
            lines.append(
                "Préférences de l'athlète — RESPECTE-LES pour placer les séances : "
                + ", ".join(prefs) + "."
            )

    lines.append(
        "Respecte la polarisation 80/20 : au plus une séance Z4-Z5 par semaine, "
        "et au moins un jour de récupération ou d'endurance facile encadrant. "
        "Pas plus de 6 séances par semaine."
    )
    return "\n".join(lines)


async def _generate_week_with_llm(
    week_index: int,
    total_weeks: int,
    dates: list[_dt.date],
    objective_type: str,
    weeks_to_event: int | None,
    ctl_current: float,
    focus: str | None,
    is_taper: bool,
    is_recovery_week: bool,
    availability: Availability | None,
    adaptation: str = "",
    emphasis: str = "",
) -> list[Workout] | None:
    """Tente une génération LLM avec retry. Retourne ``None`` si échec définitif."""
    system = _build_system_prompt()
    user = _build_user_prompt(
        week_index,
        total_weeks,
        dates,
        objective_type,
        weeks_to_event,
        ctl_current,
        focus,
        is_taper,
        is_recovery_week,
        availability,
        adaptation,
        emphasis,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for _attempt in range(2):
        raw = await chat_structured(messages, timeout_s=_GENERATION_TIMEOUT_S)
        if raw is None:
            continue
        try:
            parsed = LLMWeekPlan.model_validate(raw)
        except ValidationError:
            # On donne une seconde chance avec un rappel du format attendu.
            messages.append({"role": "assistant", "content": json.dumps(raw)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Le JSON renvoyé ne respecte pas le schéma demandé. "
                        "Renvoie strictement la structure attendue, sans texte "
                        "additionnel."
                    ),
                }
            )
            continue
        if not parsed.workouts:
            continue
        return [_expand_to_workout(draft, week_index, focus) for draft in parsed.workouts]
    return None


def _fallback_week(
    week_index: int,
    total_weeks: int,
    week_start: _dt.date,
    target_date: _dt.date | None,
    ctl_current: float,
    availability: Availability | None,
    target_event_type: str,
    focus: str | None,
    sessions_per_week: int,
    min_ctl: float = 20.0,
) -> list[Workout]:
    """Fallback déterministe pour une seule semaine.

    Génère le plan complet puis isole les séances tombant dans la fenêtre
    ``[week_start, week_start + 7j)``. C'est inefficace en théorie mais le
    builder est rapide (~ms) et cela garantit une cohérence parfaite avec le
    reste du plan déterministe.
    """
    plan = build_training_plan(
        target_date=target_date,
        ctl_current=ctl_current,
        sessions_per_week=sessions_per_week,
        availability=availability,
        target_event_type=target_event_type,
        focus=focus,
        start_date=week_start,
        fallback_weeks=max(1, total_weeks - week_index),
        min_ctl=min_ctl,
    )
    week_end = week_start + _dt.timedelta(days=7)
    return [w for w in plan if week_start <= _dt.date.fromisoformat(w.date) < week_end]


@dataclass
class GenerationContext:
    """Paramètres globaux d'une génération."""

    sessions_per_week: int
    focus: str | None
    target_date: _dt.date | None
    target_event_type: str
    ctl_current: float
    availability: Availability | None
    today: _dt.date
    min_ctl: float = 20.0
    # Contexte adaptatif (optionnel) : réalité de la semaine écoulée.
    tsb: float | None = None
    readiness_median: float | None = None
    hrv_delta_pct: float | None = None
    compliance: dict[str, Any] | None = None
    adapt_decision: str | None = None  # "reduce" | "maintain" | "progress"
    adapt_reason: str | None = None


def _build_adaptation_text(ctx: GenerationContext) -> str:
    """Bloc texte décrivant l'état réel (semaine écoulée + récupération)."""
    lines: list[str] = []
    if ctx.tsb is not None:
        lines.append(f"TSB courant : {ctx.tsb:.1f} pts.")
    if ctx.readiness_median is not None:
        lines.append(f"Readiness médiane sur 7 j : {ctx.readiness_median:.0f}/100.")
    if ctx.hrv_delta_pct is not None:
        lines.append(f"Dérive HRV vs baseline 14 j : {ctx.hrv_delta_pct:+.1f} %.")
    compliance = ctx.compliance or {}
    if compliance:
        lines.append(
            "Semaine écoulée (fait/manqué) : "
            f"{compliance.get('done', 0)} faite(s), {compliance.get('partial', 0)} partielle(s), "
            f"{compliance.get('missed', 0)} manquée(s), "
            f"{compliance.get('skipped_by_decision', 0)} repos coach ; "
            f"TSS planifié {compliance.get('planned_tss', 0.0)} vs réalisé "
            f"{compliance.get('realized_tss', 0.0)}."
        )
    if ctx.adapt_decision:
        lines.append(
            f"Ajustement décidé par la revue hebdo : {ctx.adapt_decision.upper()}"
            + (f" — {ctx.adapt_reason}" if ctx.adapt_reason else "")
        )
    return "\n".join(lines)


def _resolve_total_weeks(ctx: GenerationContext) -> int:
    if ctx.target_date is None:
        return 4
    days = (ctx.target_date - ctx.today).days
    return max(1, (days + 6) // 7)


async def generate_plan_stream(
    ctx: GenerationContext,
) -> AsyncIterator[GeneratedWeek]:
    """Génère le plan semaine par semaine en streamant chaque semaine validée.

    Pour chaque semaine, on tente la génération LLM ; si elle échoue, on
    bascule sur le builder déterministe pour cette semaine *uniquement*.
    Chaque semaine est validée par ``validate_and_correct`` avant d'être
    yieldée.
    """
    total_weeks = _resolve_total_weeks(ctx)
    week_start = ctx.today - _dt.timedelta(days=ctx.today.weekday())
    adaptation = _build_adaptation_text(ctx)
    flavor = _objective_flavor(ctx.target_event_type)
    emphasis = _training_emphasis(ctx.target_event_type)
    taper_weeks = int(flavor["taper_weeks"])

    for week_index in range(total_weeks):
        cur_week_start = week_start + _dt.timedelta(days=week_index * 7)
        cur_week_end = cur_week_start + _dt.timedelta(days=7)
        # On ne génère pas pour des jours déjà passés (utile en semaine 0).
        future_dates = [
            d
            for d in _week_dates(cur_week_start, ctx.availability)
            if d >= ctx.today and d < cur_week_end
        ]
        if not future_dates:
            continue

        weeks_to_event = (
            (ctx.target_date - cur_week_start).days // 7 if ctx.target_date is not None else None
        )
        is_taper = weeks_to_event is not None and taper_weeks > 0 and weeks_to_event < taper_weeks
        is_recovery_week = (week_index % 4 == 3) and not is_taper

        try:
            llm_workouts = await _generate_week_with_llm(
                week_index=week_index,
                total_weeks=total_weeks,
                dates=future_dates,
                objective_type=ctx.target_event_type,
                weeks_to_event=weeks_to_event,
                ctl_current=ctx.ctl_current,
                focus=ctx.focus,
                is_taper=is_taper,
                is_recovery_week=is_recovery_week,
                availability=ctx.availability,
                adaptation=adaptation,
                emphasis=emphasis,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort, fallback couvre
            llm_workouts = None

        if llm_workouts:
            source: Literal["llm", "fallback"] = "llm"
            workouts = llm_workouts
        else:
            source = "fallback"
            workouts = _fallback_week(
                week_index=week_index,
                total_weeks=total_weeks,
                week_start=cur_week_start,
                target_date=ctx.target_date,
                ctl_current=ctx.ctl_current,
                availability=ctx.availability,
                target_event_type=ctx.target_event_type,
                focus=ctx.focus,
                sessions_per_week=ctx.sessions_per_week,
                min_ctl=ctx.min_ctl,
            )

        # Validation déterministe avant émission. Le week_idx est ancré sur le
        # vrai début du plan (sinon, semaine validée isolément → week_idx 0 →
        # plafonds TSS plats et pas de progression).
        corrected, adjustments = validate_and_correct(
            workouts,
            ctl_current=ctx.ctl_current,
            availability=ctx.availability,
            target_event_type=ctx.target_event_type,
            total_weeks=total_weeks,
            min_ctl=ctx.min_ctl,
            plan_start_iso=week_start.isoformat(),
        )
        yield GeneratedWeek(
            week_index=week_index,
            workouts=corrected,
            source=source,
            adjustments=adjustments,
        )


async def collect_plan(ctx: GenerationContext) -> tuple[list[Workout], list[GeneratedWeek]]:
    """Variante non-streamée : agrège toutes les semaines.

    Utile pour les tests qui veulent récupérer le plan complet en un seul
    appel, ou pour une route REST classique non-SSE.
    """
    weeks: list[GeneratedWeek] = []
    plan: list[Workout] = []
    async for generated in generate_plan_stream(ctx):
        weeks.append(generated)
        plan.extend(generated.workouts)
    return plan, weeks


def build_context_from_app_state(
    sessions_per_week: int,
    focus: str | None,
    today: _dt.date | None = None,
    *,
    ctx: AthleteContext | None = None,
) -> GenerationContext:
    """Construit le contexte à partir de l'état persistant de l'app.

    Charge l'objectif, la disponibilité et calcule le CTL courant — même
    logique que ``build_and_save_plan`` du module ``plan_storage``.
    """
    from domestique_ai.athlete_context import context_from_env
    from domestique_ai.config import get_plan_min_ctl
    from domestique_ai.llm.availability import load_availability
    from domestique_ai.llm.objectives import load_objective
    from domestique_ai.processing.analyzer import (
        calculate_ctl_atl_tsb,
        fetch_activities_from_db,
    )

    ctx = ctx or context_from_env()
    today = today or _dt.date.today()
    activities = fetch_activities_from_db(ctx=ctx)
    curves = calculate_ctl_atl_tsb(activities, end_date=today)
    ctl_current = float(curves[-1]["CTL"]) if curves else 0.0

    objective = load_objective(ctx.objective_path)
    target_date: _dt.date | None = None
    target_event_type = "cyclosportive"
    if objective is not None:
        target_event_type = objective.type
        if objective.date:
            try:
                target_date = _dt.date.fromisoformat(objective.date)
            except ValueError:
                target_date = None

    availability = load_availability(ctx.availability_path)

    # Contexte adaptatif (best-effort, ne lève pas) : TSB, readiness médiane,
    # compliance de la semaine écoulée.
    tsb = None
    readiness_median = None
    hrv_delta_pct = None
    compliance = None
    try:
        if curves:
            tsb = round(float(curves[-1].get("TSB", 0.0)), 1)
        from domestique_ai.processing.morning_metrics import (
            compute_baselines,
            fetch_morning_history,
        )

        history = fetch_morning_history(days=14, db_path=ctx.db_path)
        last_week = [e for e in history if e["date"] >= (today - _dt.timedelta(days=7)).isoformat()]
        readiness_values = [e["readiness_score"] for e in last_week if e.get("readiness_score") is not None]
        if readiness_values:
            readiness_median = round(statistics.median(readiness_values), 1)
        baseline = compute_baselines("hrv_ms", db_path=ctx.db_path)
        if baseline.get("available"):
            hrv_delta_pct = round(baseline["delta_pct"], 1)
        from domestique_ai.llm.plan_storage import list_decisions, load_active_plan
        from domestique_ai.processing.compliance import compute_week_compliance

        plan_meta = load_active_plan(ctx.db_path)
        if plan_meta is not None:
            plan_id, workouts = plan_meta
            week_start = today - _dt.timedelta(days=today.weekday() - 7)
            compliance = compute_week_compliance(
                workouts,
                activities,
                week_start=week_start,
                decisions=list_decisions(plan_id, db_path=ctx.db_path),
            )
    except Exception:  # noqa: BLE001 — contexte enrichi best-effort
        pass

    return GenerationContext(
        sessions_per_week=sessions_per_week,
        focus=focus,
        target_date=target_date,
        target_event_type=target_event_type,
        ctl_current=ctl_current,
        availability=availability,
        today=today,
        min_ctl=get_plan_min_ctl(),
        tsb=tsb,
        readiness_median=readiness_median,
        hrv_delta_pct=hrv_delta_pct,
        compliance=compliance,
    )


def fallback_default_duration(kind: str) -> int:
    """Exposé pour les tests : durée de référence du builder déterministe."""
    return _BASE_DURATION_MIN.get(kind, 60)


__all__ = [
    "GeneratedWeek",
    "GenerationContext",
    "LLMWeekPlan",
    "LLMWorkoutDraft",
    "build_context_from_app_state",
    "collect_plan",
    "fallback_default_duration",
    "generate_plan_stream",
]


# Marqueurs internes pour silencer les imports « pour réexport » utilisés par
# le test suite et les annotations.
_ = (Any,)
