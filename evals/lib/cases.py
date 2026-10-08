"""Chargement et validation des cas d'évaluation (YAML)."""

from __future__ import annotations

from pathlib import Path

import yaml

from evals.lib.models import EvalCase

DEFAULT_CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


class CaseLoadError(ValueError):
    """Erreur de chargement d'un cas (YAML invalide, id dupliqué, schéma…)."""


def load_case(path: Path) -> EvalCase:
    """Charge un cas depuis un fichier YAML, avec validation stricte."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseLoadError(f"{path} : YAML invalide : {exc}") from exc
    if not isinstance(payload, dict):
        raise CaseLoadError(f"{path} : le cas doit être un mapping YAML")
    try:
        return EvalCase(**payload)
    except Exception as exc:
        raise CaseLoadError(f"{path} : cas invalide : {exc}") from exc


def load_cases(cases_dir: Path | None = None) -> list[EvalCase]:
    """Charge tous les cas d'un dossier, triés par nom de fichier."""
    directory = cases_dir or DEFAULT_CASES_DIR
    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in paths:
        case = load_case(path)
        if case.id in seen:
            raise CaseLoadError(f"{path} : id de cas dupliqué {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise CaseLoadError(f"aucun cas YAML trouvé dans {directory}")
    return cases
