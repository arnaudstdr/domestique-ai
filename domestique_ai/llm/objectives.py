"""
Lecture/écriture de l'objectif d'entraînement courant.

L'objectif est stocké dans un fichier YAML simple (data/objective.yaml,
gitignoré). Un template versionné existe en data/objective.yaml.example.

Le LLM coach y accède via le tool `get_objective`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from domestique_ai.config import get_objective_path

VALID_TYPES = {"cyclosportive", "course", "cyclo", "maintenance"}


def _coerce_date(value: Any) -> str | None:
    """PyYAML parse YYYY-MM-DD en datetime.date — on stocke toujours une chaîne ISO."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


@dataclass
class Objective:
    """Objectif d'entraînement structuré."""

    type: str = "maintenance"
    date: str | None = None
    distance_km: float | None = None
    elevation_m: float | None = None
    target_ftp: float | None = None
    target_avg_hr_zone: str | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {})
        data.update(extra)
        return {k: v for k, v in data.items() if v not in (None, "")}


class ObjectiveError(ValueError):
    """Erreur de validation ou de lecture de l'objectif."""


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    obj_type = payload.get("type", "maintenance")
    if obj_type not in VALID_TYPES:
        raise ObjectiveError(f"type invalide: {obj_type!r}. Attendu: {sorted(VALID_TYPES)}")
    return payload


def load_objective(path: Path | None = None) -> Objective | None:
    """Charge l'objectif depuis le YAML. Retourne None si le fichier n'existe pas."""
    target = path or get_objective_path()
    if not target.exists():
        return None
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        raise ObjectiveError(f"Le fichier {target} doit contenir un dictionnaire YAML.")
    payload = _validate(raw)

    known_fields = {
        "type",
        "date",
        "distance_km",
        "elevation_m",
        "target_ftp",
        "target_avg_hr_zone",
        "notes",
    }
    extra = {k: v for k, v in payload.items() if k not in known_fields}
    return Objective(
        type=payload.get("type", "maintenance"),
        date=_coerce_date(payload.get("date")),
        distance_km=payload.get("distance_km"),
        elevation_m=payload.get("elevation_m"),
        target_ftp=payload.get("target_ftp"),
        target_avg_hr_zone=payload.get("target_avg_hr_zone"),
        notes=payload.get("notes", "") or "",
        extra=extra,
    )


def save_objective(objective: Objective, path: Path | None = None) -> Path:
    """Sérialise l'objectif au format YAML."""
    target = path or get_objective_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(objective.to_dict(), allow_unicode=True, sort_keys=False))
    return target
