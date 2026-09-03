"""
Lecture/écriture des contraintes de disponibilité hebdomadaire.

L'utilisateur peut configurer dans ``data/availability.yaml`` (gitignoré) les
jours où il peut s'entraîner, leur durée maximale et leur contexte
(indoor/outdoor). Le builder de plan (``processing.plan_builder``) consomme
cette config pour allouer intelligemment les types de séance :
- endurance longue → jour outdoor avec la plus grande dispo,
- intervalles → jour indoor (HT plus précis),
- récupération → jour court,
- tempo → reste.

Si le fichier est absent, ``load_availability()`` retourne ``None`` et le
builder retombe sur sa grille par défaut Lun/Mer/Ven/Dim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from domestique_ai.config import get_availability_path

VALID_CONTEXTS = {"indoor", "outdoor"}

# Mapping nom anglais lowercase → indice ISO weekday (0 = lundi).
_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_WEEKDAY_BY_INDEX: dict[int, str] = {v: k for k, v in _WEEKDAY_NAMES.items()}


class AvailabilityError(ValueError):
    """Erreur de validation ou de lecture de la disponibilité."""


@dataclass
class DayAvailability:
    """Disponibilité d'un jour de la semaine."""

    weekday: int  # 0..6 (lundi..dimanche)
    max_duration_min: int
    context: str  # "indoor" | "outdoor"

    @property
    def name(self) -> str:
        return _WEEKDAY_BY_INDEX.get(self.weekday, str(self.weekday))


@dataclass
class Availability:
    """Configuration hebdomadaire de l'utilisateur."""

    days: list[DayAvailability] = field(default_factory=list)
    long_endurance_day: int | None = None
    intervals_day: int | None = None

    def weekdays(self) -> list[int]:
        return [d.weekday for d in self.days]

    def get(self, weekday: int) -> DayAvailability | None:
        for d in self.days:
            if d.weekday == weekday:
                return d
        return None

    def to_dict(self) -> dict[str, Any]:
        days_dict: dict[str, dict[str, Any]] = {}
        for d in sorted(self.days, key=lambda x: x.weekday):
            days_dict[d.name] = {
                "max_duration_min": d.max_duration_min,
                "context": d.context,
            }
        out: dict[str, Any] = {"days": days_dict}
        prefs: dict[str, str] = {}
        if self.long_endurance_day is not None:
            prefs["long_endurance_day"] = _WEEKDAY_BY_INDEX[self.long_endurance_day]
        if self.intervals_day is not None:
            prefs["intervals_day"] = _WEEKDAY_BY_INDEX[self.intervals_day]
        if prefs:
            out["preferences"] = prefs
        return out


def _parse_weekday(name: Any) -> int:
    if not isinstance(name, str):
        raise AvailabilityError(f"Nom de jour invalide: {name!r} (attendu: chaîne)")
    key = name.strip().lower()
    if key not in _WEEKDAY_NAMES:
        raise AvailabilityError(
            f"Nom de jour invalide: {name!r}. Attendu: {sorted(_WEEKDAY_NAMES)}"
        )
    return _WEEKDAY_NAMES[key]


def _parse_day(name: str, payload: Any) -> DayAvailability:
    if not isinstance(payload, dict):
        raise AvailabilityError(
            f"Entrée jour {name!r} invalide: doit être un dict (max_duration_min, context)"
        )
    raw_duration = payload.get("max_duration_min")
    try:
        duration = int(raw_duration)
    except (TypeError, ValueError) as exc:
        raise AvailabilityError(
            f"max_duration_min invalide pour {name!r}: {raw_duration!r}"
        ) from exc
    if duration < 20:
        raise AvailabilityError(
            f"max_duration_min trop court pour {name!r}: {duration} min (min 20)"
        )
    context = str(payload.get("context", "")).strip().lower()
    if context not in VALID_CONTEXTS:
        raise AvailabilityError(
            f"context invalide pour {name!r}: {context!r}. Attendu: {sorted(VALID_CONTEXTS)}"
        )
    return DayAvailability(
        weekday=_parse_weekday(name),
        max_duration_min=duration,
        context=context,
    )


def _parse_preferences(prefs: Any, known_weekdays: set[int]) -> tuple[int | None, int | None]:
    """Lit les préférences optionnelles, ignorant silencieusement les jours absents."""
    if prefs is None:
        return None, None
    if not isinstance(prefs, dict):
        raise AvailabilityError("preferences doit être un dict ou être omis.")

    def _resolve(key: str) -> int | None:
        raw = prefs.get(key)
        if raw is None:
            return None
        weekday = _parse_weekday(raw)
        # Si la préférence cite un jour non listé dans `days`, on l'ignore
        # (le builder fera son fallback heuristique).
        return weekday if weekday in known_weekdays else None

    return _resolve("long_endurance_day"), _resolve("intervals_day")


def load_availability(path: Path | None = None) -> Availability | None:
    """Charge la disponibilité depuis le YAML. Retourne None si fichier absent."""
    target = path or get_availability_path()
    if not target.exists():
        return None
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        raise AvailabilityError(f"Le fichier {target} doit contenir un dictionnaire YAML.")

    days_raw = raw.get("days")
    if not isinstance(days_raw, dict) or not days_raw:
        raise AvailabilityError(f"{target}: section 'days' manquante ou vide.")

    days = [_parse_day(name, payload) for name, payload in days_raw.items()]
    days.sort(key=lambda d: d.weekday)

    known = {d.weekday for d in days}
    long_endurance, intervals = _parse_preferences(raw.get("preferences"), known)

    return Availability(
        days=days,
        long_endurance_day=long_endurance,
        intervals_day=intervals,
    )


def save_availability(availability: Availability, path: Path | None = None) -> Path:
    """Sérialise la disponibilité au format YAML."""
    target = path or get_availability_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(availability.to_dict(), allow_unicode=True, sort_keys=False))
    return target


__all__ = [
    "Availability",
    "AvailabilityError",
    "DayAvailability",
    "VALID_CONTEXTS",
    "load_availability",
    "save_availability",
]
