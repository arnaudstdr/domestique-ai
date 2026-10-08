"""Baseline du harnais d'évaluation : régression et fraîcheur des prompts.

Le baseline versionné (``evals/baseline.json``) contient l'empreinte des
prompts/schémas (``prompt_sha``) et le résultat par cas du run de référence.
La comparaison échoue si :

- l'empreinte a changé → le prompt a été modifié : re-baseline obligatoire
  après relecture du rapport (c'est le détecteur de dégradation de prompt) ;
- un cas passait au baseline et échoue maintenant → régression ;
- un cas a disparu ou est apparu → la suite a changé, baseline périmé.

Un cas qui échouait et passe est une amélioration (rapportée, non bloquante).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


class BaselineError(ValueError):
    """Erreur de lecture/comparaison de baseline."""


@dataclass(frozen=True)
class BaselineComparison:
    """Verdict de comparaison d'un run courant au baseline de référence."""

    prompt_changed: bool = False
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    missing_cases: list[str] = field(default_factory=list)
    new_cases: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.prompt_changed or self.regressions or self.missing_cases or self.new_cases)

    def summary(self) -> str:
        lines: list[str] = []
        if self.prompt_changed:
            lines.append(
                "prompt modifié : la baseline est périmée → relire le rapport puis "
                "re-baseliner (make eval-update-baseline)"
            )
        if self.regressions:
            lines.append(f"régressions : {self.regressions}")
        if self.missing_cases:
            lines.append(f"cas disparus : {self.missing_cases}")
        if self.new_cases:
            lines.append(f"nouveaux cas hors baseline : {self.new_cases}")
        if self.improvements:
            lines.append(f"améliorations (non bloquantes) : {self.improvements}")
        return " | ".join(lines) if lines else "baseline à jour, aucun écart"


def extract_case_success(results: dict[str, Any]) -> dict[str, bool]:
    """Extrait ``{case_id: success}`` d'un export JSON de promptfoo."""
    rows = ((results.get("results") or {}).get("results")) or []
    cases: dict[str, bool] = {}
    for row in rows:
        case_id = ((row.get("testCase") or {}).get("vars") or {}).get("case_id")
        if not case_id:
            continue
        cases[str(case_id)] = bool(row.get("success"))
    if not cases:
        raise BaselineError("aucun cas trouvé dans le fichier de résultats promptfoo")
    return cases


def build_baseline(results: dict[str, Any], *, prompt_sha: str) -> dict[str, Any]:
    """Construit le contenu du baseline versionné depuis un run promptfoo."""
    cases = extract_case_success(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_sha": prompt_sha,
        "provider": "stub",
        "cases": dict(sorted(cases.items())),
        "stats": {"successes": sum(cases.values()), "total": len(cases)},
    }


def compare(
    results: dict[str, Any],
    baseline: dict[str, Any],
    *,
    prompt_sha: str,
) -> BaselineComparison:
    """Compare un run courant au baseline et classe les écarts."""
    version = baseline.get("schema_version")
    if version != SCHEMA_VERSION:
        raise BaselineError(f"schema_version de baseline inconnu : {version!r}")

    current = extract_case_success(results)
    expected = {str(case_id): bool(ok) for case_id, ok in (baseline.get("cases") or {}).items()}
    if not expected:
        raise BaselineError("baseline sans cas : re-baseliner")

    regressions = sorted(
        case_id
        for case_id, was_ok in expected.items()
        if was_ok and case_id in current and not current[case_id]
    )
    improvements = sorted(
        case_id for case_id, was_ok in expected.items() if not was_ok and current.get(case_id)
    )
    return BaselineComparison(
        prompt_changed=prompt_sha != baseline.get("prompt_sha"),
        regressions=regressions,
        improvements=improvements,
        missing_cases=sorted(set(expected) - set(current)),
        new_cases=sorted(set(current) - set(expected)),
    )
