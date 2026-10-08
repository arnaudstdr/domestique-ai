"""Exécution d'un cas d'évaluation contre le vrai pipeline du coach.

Mode ``stub`` : tous les appels LLM sont scriptés (aucun réseau, aucun Ollama) ;
le reste est réel — tools, DB SQLite seedée, boucle agentique, validateurs de
plan. La date est figée sur ``case.today`` dans les modules qui lisent
``date.today()`` directement (tools, séance du jour, décision du matin).

Le résultat est une ``EvalEnvelope`` autosuffisante : elle embarque la réponse,
la trace des tools, le contexte système injecté et les attentes du cas, de
sorte que les assertions promptfoo n'aient besoin que de l'output du provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import types
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from evals.lib import prompts, seeds
from evals.lib.models import EvalCase, EvalEnvelope
from evals.lib.stub import ScriptedLLM

# Modules qui lisent ``date.today()`` directement (hors paramètres injectés).
_FROZEN_DT_TARGETS = (
    "domestique_ai.llm.tools.dt",
    "domestique_ai.processing.today._dt",
    "domestique_ai.llm.daily_decision._dt",
)


class UnsupportedProviderError(ValueError):
    """Provider d'évaluation non supporté (le mode live arrive plus tard)."""


def run_case(
    case: EvalCase,
    *,
    root: Path,
    provider: str = "stub",
) -> EvalEnvelope:
    """Exécute un cas et retourne son enveloppe de résultat."""
    if provider != "stub":
        raise UnsupportedProviderError(f"provider inconnu : {provider!r}")
    seed_paths = seeds.seed_scenario(root, case.scenario)
    ctx = _athlete_context(case, seed_paths)
    if case.kind == "plan":
        return _run_plan(case, ctx)
    return _run_chat(case, ctx)


async def _consume(iterator: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event async for event in iterator]


@contextlib.contextmanager
def _frozen_today(day: dt.date) -> Iterator[None]:
    """Gèle ``date.today()`` dans les modules qui l'appellent directement."""
    frozen_cls = type("FrozenDate", (dt.date,), {"today": classmethod(lambda cls: day)})
    shim = types.SimpleNamespace(date=frozen_cls, datetime=dt.datetime, timedelta=dt.timedelta)
    with contextlib.ExitStack() as stack:
        for target in _FROZEN_DT_TARGETS:
            module_path, attr = target.rsplit(".", 1)
            stack.enter_context(mock.patch(f"{module_path}.{attr}", shim))
        yield


def _stub_patches(scripted: ScriptedLLM) -> list[Any]:
    """Neutralise tout appel LLM non scripté (fallbacks déterministes)."""
    from domestique_ai.llm import coach, daily_brief, memory, ollama_client, plan_generator
    from domestique_ai.processing import today as today_module

    async def _no_structured(*args: Any, **kwargs: Any) -> None:
        return None

    def _no_structured_sync(*args: Any, **kwargs: Any) -> None:
        return None

    def _fake_embed(texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 26 for _ in texts]

    def _no_today_llm(dossier: dict[str, Any]) -> None:
        return None

    return [
        mock.patch.object(coach, "stream_chat", scripted.stream_chat),
        mock.patch.object(plan_generator, "chat_structured", scripted.chat_structured),
        mock.patch.object(ollama_client, "chat_structured", _no_structured),
        mock.patch.object(ollama_client, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(daily_brief, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(memory, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(memory, "embed_texts_sync", _fake_embed),
        mock.patch.object(today_module, "_decide_kind_with_llm", _no_today_llm),
    ]


def _athlete_context(case: EvalCase, seed_paths: seeds.SeedPaths) -> Any:
    from domestique_ai.athlete_context import AthleteContext

    return AthleteContext(
        db_path=seed_paths.db_path,
        profile_path=seed_paths.profile_path,
        objective_path=seed_paths.objective_path,
        availability_path=seed_paths.availability_path,
        ftp=case.ftp,
        hr_rest=50.0,
        hr_max=190.0,
        sex="M",
        lthr_pct=0.88,
        level=case.level,
    )


def _run_chat(case: EvalCase, ctx: Any) -> EvalEnvelope:
    from domestique_ai.llm import coach

    scripted = ScriptedLLM(turns=case.stub)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_frozen_today(case.today))
        for patch in _stub_patches(scripted):
            stack.enter_context(patch)
        events = asyncio.run(
            _consume(coach.run_turn_stream(case.user or "", case.history or None, ctx=ctx))
        )
    final = next((event for event in reversed(events) if event["type"] == "final"), None)
    return EvalEnvelope(
        case_id=case.id,
        kind="chat",
        mode="stub",
        prompt_sha=prompts.compute_prompt_sha(),
        model="stub",
        answer=str((final or {}).get("content") or ""),
        tool_trace=list((final or {}).get("tool_trace") or []),
        system_messages=scripted.system_messages(),
        user_message=case.user or "",
        events=[event["type"] for event in events],
        stub_calls=len(scripted.calls),
        stub_exhausted=scripted.exhausted,
        expectations=case.expectations.model_dump(mode="json"),
    )


def _run_plan(case: EvalCase, ctx: Any) -> EvalEnvelope:
    from domestique_ai.llm import plan_generator as pg

    spec = case.plan
    assert spec is not None  # garanti par la validation d'EvalCase
    scripted = ScriptedLLM(structured=spec.stub)
    availability = spec.availability.to_availability() if spec.availability else None
    generation = pg.GenerationContext(
        sessions_per_week=spec.sessions_per_week,
        focus=None,
        target_date=spec.target_date,
        target_event_type=spec.target_event_type,
        ctl_current=spec.ctl_current,
        availability=availability,
        today=spec.today,
        min_ctl=spec.min_ctl,
        level=spec.level or case.level,
    )
    with contextlib.ExitStack() as stack:
        stack.enter_context(_frozen_today(case.today))
        for patch in _stub_patches(scripted):
            stack.enter_context(patch)
        _plan, weeks = asyncio.run(pg.collect_plan(generation))

    payload: dict[str, Any] = {
        "weeks": [
            {
                "week_index": week.week_index,
                "source": week.source,
                "adjustments": list(week.adjustments),
                "workouts": [workout.to_dict() for workout in week.workouts],
            }
            for week in weeks
        ],
        "availability_days": (
            [
                {"weekday": day.weekday, "max_duration_min": day.max_duration_min}
                for day in spec.availability.days
            ]
            if spec.availability
            else []
        ),
        "ctl_current": spec.ctl_current,
        "min_ctl": spec.min_ctl,
        "level": spec.level or case.level,
    }
    return EvalEnvelope(
        case_id=case.id,
        kind="plan",
        mode="stub",
        prompt_sha=prompts.compute_prompt_sha(),
        model="stub",
        answer="",
        events=[],
        stub_calls=len(scripted.structured_calls),
        stub_exhausted=scripted.exhausted,
        expectations=case.expectations.model_dump(mode="json"),
        plan=payload,
    )
