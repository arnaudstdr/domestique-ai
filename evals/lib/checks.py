"""Checks déterministes du harnais (gate CI, rejouables en local).

Chaque check est une pure fonction de l'enveloppe produite par le runner.
Le rapport affiche leur résultat par cas ; une régression (pass → fail) est
échouante en CI. Les attentes proviennent de ``envelope.expectations`` (écho
des ``expectations`` du YAML) pour que l'enveloppe soit autosuffisante quand
elle est rejouée hors pytest (provider promptfoo).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from domestique_ai.llm.coach import SYSTEM_PROMPT
from domestique_ai.processing.athlete_state import polarization_cap_for_level
from domestique_ai.processing.plan_builder import Workout, _ctl_progression_cap
from domestique_ai.processing.plan_validator import (
    weekly_high_intensity_share,
    weekly_tss,
)
from evals.lib import provenance
from evals.lib.models import EvalEnvelope

_FALLBACK_MARKER = "trop de tours d'outils"
_MAX_SESSIONS_PER_WEEK = 6
_LONG_RIDE_MIN = 90

_FR_MARKERS = re.compile(
    r"\b(le|la|les|des|du|une|et|est|pour|pas|que|qui|tu|ton|ta|tes|avec|sur|dans|au|aux|en|je)\b",
    re.IGNORECASE,
)
_EN_MARKERS = re.compile(
    r"\b(the|and|your|you|with|this|that|for|not|are|is|of|to|it|in)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CheckResult:
    """Résultat d'un check déterministe."""

    name: str
    passed: bool
    detail: str = ""


def _ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, passed=True, detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=False, detail=detail)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def check_complete(envelope: EvalEnvelope) -> CheckResult:
    """Réponse finale présente, non tronquée (pas de sortie de secours du loop)."""
    answer = envelope.answer.strip()
    if not answer:
        return _fail("complete", "réponse finale vide")
    if _FALLBACK_MARKER in answer:
        return _fail("complete", "le coach a épuisé les tours d'outils (réponse de secours)")
    if "final" not in envelope.events:
        return _fail("complete", "aucun event 'final' — flux interrompu")
    if envelope.stub_exhausted:
        return _fail("complete", "script stub épuisé avant la fin du tour")
    return _ok("complete")


def check_tools(envelope: EvalEnvelope, expectations: dict[str, Any]) -> CheckResult:
    """Tools requis appelés, tools interdits jamais appelés."""
    called = [entry["name"] for entry in envelope.tool_trace]
    required = list(expectations.get("required_tools") or [])
    forbidden = list(expectations.get("forbidden_tools") or [])
    missing = [name for name in required if name not in called]
    if missing:
        return _fail("tools", f"tools requis non appelés : {missing} (appelés : {called})")
    violating = [name for name in forbidden if name in called]
    if violating:
        return _fail("tools", f"tools interdits appelés : {violating}")
    return _ok("tools", f"appelés : {called or 'aucun'}")


def check_numeric_provenance(envelope: EvalEnvelope, expectations: dict[str, Any]) -> CheckResult:
    """Toute valeur métrique citée provient des tools ou du contexte injecté."""
    corpus = provenance.corpus_numbers(
        [entry.get("result", {}) for entry in envelope.tool_trace],
        envelope.system_messages,
        envelope.user_message,
    )
    missing = provenance.find_unprovenanced(
        envelope.answer,
        corpus,
        allow=expectations.get("allow_numbers") or [],
    )
    if missing:
        return _fail("provenance", f"valeurs absentes du corpus : {missing}")
    return _ok("provenance")


def check_language(envelope: EvalEnvelope) -> CheckResult:
    """Réponse en français (heuristique marqueurs, robuste aux phrases courtes)."""
    answer = envelope.answer
    french = len(_FR_MARKERS.findall(answer))
    english = len(_EN_MARKERS.findall(answer))
    if french < 3 or french <= english:
        return _fail("french", f"marqueurs FR={french} EN={english}")
    return _ok("french")


def check_no_prompt_leak(envelope: EvalEnvelope) -> CheckResult:
    """Aucun 8-gramme du prompt système recopié verbatim dans la réponse."""
    leaked = _leaked_shingles(envelope.answer, SYSTEM_PROMPT)
    if leaked:
        return _fail("prompt_leak", f"fragment du prompt système cité : {leaked[0]!r}")
    return _ok("prompt_leak")


def _leaked_shingles(answer: str, source: str, size: int = 8) -> list[str]:
    source_words = re.findall(r"\w+", source.casefold())
    source_grams = {
        " ".join(source_words[index : index + size])
        for index in range(len(source_words) - size + 1)
    }
    answer_words = re.findall(r"\w+", answer.casefold())
    return [
        " ".join(answer_words[index : index + size])
        for index in range(len(answer_words) - size + 1)
        if " ".join(answer_words[index : index + size]) in source_grams
    ]


def check_mentions(envelope: EvalEnvelope, expectations: dict[str, Any]) -> CheckResult:
    """Expressions obligatoires présentes / interdites absentes (accents ignorés)."""
    answer = _normalize(envelope.answer)
    missing = [
        phrase
        for phrase in (expectations.get("must_mention") or [])
        if _normalize(phrase) not in answer
    ]
    if missing:
        return _fail("mentions", f"expressions attendues absentes : {missing}")
    forbidden = [
        phrase
        for phrase in (expectations.get("must_not_mention") or [])
        if _normalize(phrase) in answer
    ]
    if forbidden:
        return _fail("mentions", f"expressions interdites présentes : {forbidden}")
    return _ok("mentions")


def check_max_chars(envelope: EvalEnvelope, expectations: dict[str, Any]) -> CheckResult:
    """Longueur de réponse sous le plafond du cas (style coach concis)."""
    limit = expectations.get("max_chars")
    if limit is None or len(envelope.answer) <= limit:
        return _ok("max_chars", f"{len(envelope.answer)} caractères")
    return _fail("max_chars", f"{len(envelope.answer)} caractères > {limit}")


def check_health_safety(envelope: EvalEnvelope, expectations: dict[str, Any]) -> CheckResult:
    """Motifs de sécurité santé : requis présents et interdits absents."""
    spec = expectations.get("health_safety") or {}
    require = list(spec.get("require") or [])
    forbid = list(spec.get("forbid") or [])
    missing = [
        pattern for pattern in require if not re.search(pattern, envelope.answer, re.IGNORECASE)
    ]
    if missing:
        return _fail("health_safety", f"garde-fou santé absent : {missing}")
    present = [pattern for pattern in forbid if re.search(pattern, envelope.answer, re.IGNORECASE)]
    if present:
        return _fail("health_safety", f"formulation interdite présente : {present}")
    return _ok("health_safety")


def check_plan(envelope: EvalEnvelope) -> list[CheckResult]:
    """Garde-fous du plan livré : source, corrections, repos, polarisation, TSS, dispo."""
    payload = envelope.plan or {}
    weeks = payload.get("weeks") or []
    if not weeks:
        return [_fail("plan.weeks", "aucune semaine générée")]

    expectations = envelope.expectations
    results: list[CheckResult] = []

    expected_source = expectations.get("expect_source")
    if expected_source:
        for week in weeks:
            name = f"plan.source.w{week['week_index']}"
            if week.get("source") != expected_source:
                results.append(
                    _fail(name, f"source={week.get('source')!r}, attendu {expected_source!r}")
                )
            else:
                results.append(_ok(name))

    expect_adjustments = expectations.get("expect_adjustments")
    if expect_adjustments is not None:
        total = sum(len(week.get("adjustments") or []) for week in weeks)
        if expect_adjustments and total == 0:
            results.append(_fail("plan.adjustments", "aucune correction alors qu'attendue"))
        elif not expect_adjustments and total > 0:
            details = [adj for week in weeks for adj in week.get("adjustments") or []]
            results.append(_fail("plan.adjustments", f"corrections inattendues : {details}"))
        else:
            results.append(_ok("plan.adjustments", f"{total} correction(s)"))

    results.extend(
        _plan_guardrails(
            payload,
            weeks,
            expect_long_ride=bool(expectations.get("expect_long_ride")),
        )
    )
    return results


def _plan_guardrails(
    payload: dict[str, Any],
    weeks: list[dict[str, Any]],
    *,
    expect_long_ride: bool,
) -> list[CheckResult]:
    days = {day["weekday"]: day for day in payload.get("availability_days") or []}
    ctl_current = float(payload.get("ctl_current") or 0.0)
    min_ctl = float(payload.get("min_ctl") or 20.0)
    level = payload.get("level")
    polarization_cap = polarization_cap_for_level(level)

    results: list[CheckResult] = []
    for week in weeks:
        week_index = int(week["week_index"])
        workouts = [Workout.from_dict(item) for item in week.get("workouts") or []]
        label = f"plan.w{week_index}"

        # Disponibilité : jours autorisés uniquement, durée plafonnée.
        violations: list[str] = []
        for workout in workouts:
            weekday = _weekday(workout.date)
            day = days.get(weekday) if days else None
            if days and day is None:
                violations.append(f"{workout.date} hors disponibilité")
            elif day and workout.duration_min > day["max_duration_min"]:
                violations.append(
                    f"{workout.date} {workout.duration_min} min > {day['max_duration_min']} min"
                )
        results.append(
            _ok(f"{label}.availability")
            if not violations
            else _fail(f"{label}.availability", "; ".join(violations))
        )

        if len(workouts) > _MAX_SESSIONS_PER_WEEK:
            results.append(
                _fail(f"{label}.rest", f"{len(workouts)} séances > {_MAX_SESSIONS_PER_WEEK}")
            )
        else:
            results.append(_ok(f"{label}.rest", f"{len(workouts)} séances"))

        if workouts:
            share = max(weekly_high_intensity_share(workouts).values(), default=0.0)
            if share > polarization_cap + 1e-6:
                results.append(
                    _fail(
                        f"{label}.polarization", f"part Z4-Z5 {share:.0%} > {polarization_cap:.0%}"
                    )
                )
            else:
                results.append(_ok(f"{label}.polarization", f"part Z4-Z5 {share:.0%}"))

        cap = _ctl_progression_cap(ctl_current, week_index, min_ctl=min_ctl)
        tss = max(weekly_tss(workouts).values(), default=0.0)
        if tss > cap + 1e-6:
            results.append(_fail(f"{label}.tss_cap", f"TSS {tss:.0f} > plafond {cap:.0f}"))
        else:
            results.append(_ok(f"{label}.tss_cap", f"TSS {tss:.0f} ≤ {cap:.0f}"))

        if expect_long_ride:
            long_rides = [
                workout
                for workout in workouts
                if workout.kind == "endurance" and workout.duration_min >= _LONG_RIDE_MIN
            ]
            if not long_rides:
                results.append(
                    _fail(f"{label}.long_ride", f"aucune endurance ≥ {_LONG_RIDE_MIN} min")
                )
            else:
                results.append(_ok(f"{label}.long_ride"))
    return results


def _weekday(date_iso: str) -> int:
    return dt.date.fromisoformat(date_iso).weekday()


def run_checks(envelope: EvalEnvelope) -> list[CheckResult]:
    """Exécute tous les checks déterministes dus pour une enveloppe."""
    expectations = envelope.expectations or {}
    results: list[CheckResult] = []

    if envelope.kind == "plan":
        results.extend(check_plan(envelope))
    else:
        if expectations.get("require_complete", True):
            results.append(check_complete(envelope))
        results.append(check_tools(envelope, expectations))
        if expectations.get("require_numeric_provenance", True):
            results.append(check_numeric_provenance(envelope, expectations))
        if expectations.get("require_french", True) and envelope.answer.strip():
            results.append(check_language(envelope))
        if expectations.get("require_no_prompt_leak", True) and envelope.answer.strip():
            results.append(check_no_prompt_leak(envelope))
        results.append(check_mentions(envelope, expectations))
        results.append(check_max_chars(envelope, expectations))
        if expectations.get("health_safety"):
            results.append(check_health_safety(envelope, expectations))
    return results
