"""Modèles Pydantic des cas d'évaluation et enveloppe de résultat.

Un cas décrit un scénario rejoué par le harnais : données seedées, message
utilisateur, réponses LLM scriptées (mode stub) et attentes (checks
déterministes du gate CI). L'enveloppe transporte tout ce qu'une assertion
peut inspecter : réponse finale, trace des tools, contexte injecté, plan.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base : champs inconnus interdits (un YAML de cas doit être explicite)."""

    model_config = ConfigDict(extra="forbid")


class ToolCallSpec(StrictModel):
    """Un appel de tool scripté dans le mode stub."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class StubTurn(StrictModel):
    """Une réponse scriptée du LLM (un tour de la boucle agentique)."""

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)


class HealthSafety(StrictModel):
    """Motifs requis / interdits pour les cas sensibles (regex, insensible à la casse)."""

    require: list[str] = Field(default_factory=list)
    forbid: list[str] = Field(default_factory=list)


class Expectations(StrictModel):
    """Attentes déterministes d'un cas (gate CI).

    ``allow_numbers`` autorise explicitement des valeurs chiffrées hors corpus
    (outil ou contexte injecté) — à n'utiliser qu'en connaissance de cause.
    Les trois champs ``expect_*`` ne concernent que les cas de type ``plan``.
    """

    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)
    allow_numbers: list[float] = Field(default_factory=list)
    max_chars: int | None = None
    require_french: bool = True
    require_complete: bool = True
    require_no_prompt_leak: bool = True
    require_numeric_provenance: bool = True
    health_safety: HealthSafety | None = None
    expect_source: Literal["llm", "fallback"] | None = None
    expect_adjustments: bool | None = None
    expect_long_ride: bool = False


class DaySpec(StrictModel):
    """Un jour disponible dans la semaine type de l'athlète."""

    weekday: int = Field(ge=0, le=6)
    max_duration_min: int = Field(ge=20)
    context: Literal["indoor", "outdoor"] = "outdoor"


class AvailabilitySpec(StrictModel):
    """Disponibilité hebdomadaire d'un cas plan."""

    days: list[DaySpec]
    long_endurance_day: int | None = Field(default=None, ge=0, le=6)
    intervals_day: int | None = Field(default=None, ge=0, le=6)

    def to_availability(self) -> Any:
        """Construit l'objet ``Availability`` attendu par le générateur de plan."""
        from domestique_ai.llm.availability import Availability, DayAvailability

        return Availability(
            days=[
                DayAvailability(
                    weekday=day.weekday,
                    max_duration_min=day.max_duration_min,
                    context=day.context,
                )
                for day in self.days
            ],
            long_endurance_day=self.long_endurance_day,
            intervals_day=self.intervals_day,
        )


class PlanSpec(StrictModel):
    """Paramètres d'un cas de génération de plan (LLM structuré scripté)."""

    today: dt.date
    target_date: dt.date
    ctl_current: float = 60.0
    min_ctl: float = 20.0
    sessions_per_week: int = 4
    target_event_type: str = "cyclosportive"
    level: str | None = None
    availability: AvailabilitySpec | None = None
    stub: list[Any] = Field(min_length=1)


class JudgeSpec(StrictModel):
    """Rubrique du juge LLM local (reporting uniquement, jamais le gate CI).

    Activée par ``EVAL_JUDGE=1`` (voir ``evals/cases.py``) ; le modèle est
    ``EVAL_JUDGE_MODEL`` > ``OLLAMA_MODEL`` > défaut de l'app.
    """

    rubric: str
    model: str | None = None


class EvalCase(StrictModel):
    """Un cas d'évaluation chargé depuis un YAML."""

    id: str
    title: str = ""
    scenario: str = "empty"
    kind: Literal["chat", "plan"] = "chat"
    today: dt.date = dt.date(2026, 4, 30)
    level: str = "intermediate"
    ftp: float = 250.0
    user: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    stub: list[StubTurn] = Field(default_factory=list)
    plan: PlanSpec | None = None
    judge: JudgeSpec | None = None
    expectations: Expectations = Field(default_factory=Expectations)

    @model_validator(mode="after")
    def _check_shape(self) -> EvalCase:
        if self.kind == "chat":
            if not self.user:
                raise ValueError(f"cas chat {self.id!r} : champ 'user' requis")
            if not self.stub:
                raise ValueError(f"cas chat {self.id!r} : champ 'stub' requis")
            if self.plan is not None:
                raise ValueError(f"cas chat {self.id!r} : champ 'plan' interdit")
        if self.kind == "plan" and self.plan is None:
            raise ValueError(f"cas plan {self.id!r} : champ 'plan' requis")
        return self


@dataclass
class EvalEnvelope:
    """Résultat d'exécution d'un cas, consommé par les checks et les rapports.

    ``tool_trace`` : appels réels de tools (nom, arguments, résultat) — source
    de vérité de l'anti-hallucination. ``system_messages`` : contexte système
    injecté au 1ᵉʳ appel LLM (corpus autorisé pour les valeurs chiffrées).
    """

    case_id: str
    kind: str
    mode: str
    prompt_sha: str
    model: str
    answer: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)
    user_message: str = ""
    events: list[str] = field(default_factory=list)
    stub_calls: int = 0
    stub_exhausted: bool = False
    expectations: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
