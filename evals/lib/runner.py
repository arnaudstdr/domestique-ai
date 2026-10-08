"""Exécution d'un cas d'évaluation contre le vrai pipeline du coach.

Deux modes :

- ``stub`` (défaut, gate CI) : tous les appels LLM sont scriptés — aucun
  réseau, aucun Ollama — le reste est réel (tools, DB SQLite seedée, boucle
  agentique, validateurs de plan) ;
- ``ollama`` (local/Pi, reporting) : la boucle du coach (ou la génération de
  plan) est servie par le vrai modèle local ; seules les générations LLM
  auxiliaires restent neutralisées pour que stub et live ne diffèrent que par
  le modèle évalué.

La date est figée sur ``case.today`` dans les modules qui lisent
``date.today()`` directement (tools, séance du jour, décision du matin).

Le résultat est une ``EvalEnvelope`` autosuffisante : réponse, trace des tools,
contexte système injecté et attentes du cas — les assertions promptfoo n'ont
besoin que de l'output du provider.
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

PROVIDERS = ("stub", "ollama")


class UnsupportedProviderError(ValueError):
    """Provider d'évaluation inconnu."""


def run_case(
    case: EvalCase,
    *,
    root: Path,
    provider: str = "stub",
) -> EvalEnvelope:
    """Exécute un cas et retourne son enveloppe de résultat."""
    if provider not in PROVIDERS:
        raise UnsupportedProviderError(f"provider inconnu : {provider!r} (attendu : {PROVIDERS})")
    seed_paths = seeds.seed_scenario(root, case.scenario)
    ctx = _athlete_context(case, seed_paths)
    if case.kind == "plan":
        return _run_plan(case, ctx, provider=provider)
    return _run_chat(case, ctx, provider=provider)


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


def _aux_patches() -> list[Any]:
    """Neutralise les générations LLM auxiliaires (contexte, brief, embeddings).

    L'évaluation ne mesure qu'un chemin LLM à la fois : identique en stub et en
    live pour que la comparaison ne porte que sur le modèle évalué.
    """
    from domestique_ai.llm import daily_brief, memory, ollama_client
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
        mock.patch.object(ollama_client, "chat_structured", _no_structured),
        mock.patch.object(ollama_client, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(daily_brief, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(memory, "chat_structured_sync", _no_structured_sync),
        mock.patch.object(memory, "embed_texts_sync", _fake_embed),
        mock.patch.object(today_module, "_decide_kind_with_llm", _no_today_llm),
    ]


def _stub_patches(scripted: ScriptedLLM) -> list[Any]:
    """Scripte les deux points d'entrée LLM mesurés + neutralise les auxiliaires."""
    from domestique_ai.llm import coach, plan_generator

    return [
        mock.patch.object(coach, "stream_chat", scripted.stream_chat),
        mock.patch.object(plan_generator, "chat_structured", scripted.chat_structured),
        *_aux_patches(),
    ]


def _live_chat_patches(capture: dict[str, Any]) -> list[Any]:
    """Appelle le vrai ``stream_chat`` en capturant messages et nombre d'appels."""
    from domestique_ai.llm import coach

    real = coach.stream_chat

    async def recording(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        think: bool = False,
        options: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        capture["calls"] = int(capture.get("calls") or 0) + 1
        capture.setdefault(
            "system_messages",
            [
                str(message.get("content", ""))
                for message in messages
                if message.get("role") == "system"
            ],
        )
        async for chunk in real(
            messages, tools=tools, model=model, think=think, options=options, label=label
        ):
            yield chunk

    return [mock.patch.object(coach, "stream_chat", recording), *_aux_patches()]


def _live_plan_patches(capture: dict[str, Any]) -> list[Any]:
    """Appelle le vrai ``chat_structured`` du générateur de plan en le comptant."""
    from domestique_ai.llm import plan_generator

    real = plan_generator.chat_structured

    async def recording(messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any] | None:
        capture["calls"] = int(capture.get("calls") or 0) + 1
        return await real(messages, **kwargs)

    return [mock.patch.object(plan_generator, "chat_structured", recording), *_aux_patches()]


def _live_model() -> str:
    from domestique_ai.config import get_ollama_model

    return get_ollama_model()


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


def _run_chat(case: EvalCase, ctx: Any, *, provider: str) -> EvalEnvelope:
    from domestique_ai.llm import coach

    capture: dict[str, Any] = {}
    scripted: ScriptedLLM | None = None
    if provider == "stub":
        scripted = ScriptedLLM(turns=case.stub)
        patches = _stub_patches(scripted)
        mode, model = "stub", "stub"
    else:
        patches = _live_chat_patches(capture)
        mode, model = "ollama", _live_model()

    with contextlib.ExitStack() as stack:
        stack.enter_context(_frozen_today(case.today))
        for patch in patches:
            stack.enter_context(patch)
        events = asyncio.run(
            _consume(coach.run_turn_stream(case.user or "", case.history or None, ctx=ctx))
        )
    final = next((event for event in reversed(events) if event["type"] == "final"), None)

    if scripted is not None:
        system_messages = scripted.system_messages()
        calls = len(scripted.calls)
        exhausted = scripted.exhausted
    else:
        system_messages = list(capture.get("system_messages") or [])
        calls = int(capture.get("calls") or 0)
        exhausted = False

    return EvalEnvelope(
        case_id=case.id,
        kind="chat",
        mode=mode,
        prompt_sha=prompts.compute_prompt_sha(),
        model=model,
        answer=str((final or {}).get("content") or ""),
        tool_trace=list((final or {}).get("tool_trace") or []),
        system_messages=system_messages,
        user_message=case.user or "",
        events=[event["type"] for event in events],
        stub_calls=calls,
        stub_exhausted=exhausted,
        expectations=case.expectations.model_dump(mode="json"),
    )


def _run_plan(case: EvalCase, ctx: Any, *, provider: str) -> EvalEnvelope:
    from domestique_ai.llm import plan_generator as pg

    spec = case.plan
    assert spec is not None  # garanti par la validation d'EvalCase
    capture: dict[str, Any] = {}
    scripted: ScriptedLLM | None = None
    if provider == "stub":
        scripted = ScriptedLLM(structured=spec.stub)
        patches = _stub_patches(scripted)
        mode, model = "stub", "stub"
    else:
        patches = _live_plan_patches(capture)
        mode, model = "ollama", _live_model()

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
        for patch in patches:
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
    calls = (
        len(scripted.structured_calls) if scripted is not None else int(capture.get("calls") or 0)
    )
    return EvalEnvelope(
        case_id=case.id,
        kind="plan",
        mode=mode,
        prompt_sha=prompts.compute_prompt_sha(),
        model=model,
        answer="",
        events=[],
        stub_calls=calls,
        stub_exhausted=scripted.exhausted if scripted is not None else False,
        expectations=case.expectations.model_dump(mode="json"),
        plan=payload,
    )
