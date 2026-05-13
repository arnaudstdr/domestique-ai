"""
Suggestion de séance pour la date courante.

Logique pur calcul, indépendante du LLM et indépendante du plan persisté :
- consulte la disponibilité de l'utilisateur pour aujourd'hui (durée max,
  contexte indoor/outdoor),
- lit le TSB courant depuis les activités en base,
- mappe TSB + préférences sur un type de séance (recovery / endurance /
  tempo / intervals) et construit le Workout via les helpers de
  ``plan_builder``.

Exposée :
- à l'API REST (`GET /api/coach/today`) → carte « Séance du jour » du Dashboard,
- au coach LLM via le tool `propose_workout_today`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from domestique_ai.llm.availability import (
    Availability,
    AvailabilityError,
    load_availability,
)
from domestique_ai.processing.analyzer import (
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)
from domestique_ai.processing.plan_builder import (
    _BASE_DURATION_MIN,
    _TARGET_ZONE,
    _TSS_PER_MIN,
    Workout,
    _name_for,
    _structure_for,
)

_WEEKDAY_FR: dict[int, str] = {
    0: "lundi",
    1: "mardi",
    2: "mercredi",
    3: "jeudi",
    4: "vendredi",
    5: "samedi",
    6: "dimanche",
}

# Bornes recommandées de durée de séance, indépendamment de ce que demande
# l'utilisateur : éviter une recovery de 5 min ou un endurance de 8 h.
_MIN_DURATION_MIN = 20
_MAX_DURATION_MIN = 240


def _tsb_zone_label(tsb: float) -> str:
    """Même barème que ``_tsb_zone_label`` du tool, sans emoji."""
    if tsb > 5:
        return "Frais"
    if tsb >= -10:
        return "Optimal"
    if tsb >= -20:
        return "Fatigué"
    return "Surentraîné"


def _kind_for_tsb(tsb: float, intervals_day_today: bool) -> str:
    """Détermine le type de séance à partir du TSB courant.

    Compromis :
    - TSB chroniquement bas → repos actif Z1,
    - TSB optimal/fatigué léger → endurance Z2 (le foncier qui répare ET
      construit),
    - TSB frais → tempo Z3 par défaut, ou intervalles Z4 si l'utilisateur a
      placé son jour intervalles aujourd'hui (préférence explicite).
    """
    if tsb < -10:
        return "recovery"
    if tsb <= 5:
        return "endurance"
    return "intervals" if intervals_day_today else "tempo"


def _resolve_duration(
    kind: str,
    day_max_min: int | None,
    available_min: int | None,
) -> int:
    """Croise la durée par défaut du `kind`, la dispo du jour et l'override utilisateur."""
    base = _BASE_DURATION_MIN.get(kind, 60)
    # L'override explicite gagne, puis la dispo, puis la valeur de référence.
    if available_min is not None and available_min > 0:
        duration = available_min
    elif day_max_min is not None:
        duration = min(base, day_max_min)
    else:
        duration = base
    if duration < _MIN_DURATION_MIN:
        duration = _MIN_DURATION_MIN
    if duration > _MAX_DURATION_MIN:
        duration = _MAX_DURATION_MIN
    return int(duration)


def _load_availability_safely() -> Availability | None:
    """Renvoie l'``Availability`` ou ``None`` si fichier absent / mal formé.

    Un YAML invalide ne doit pas planter la séance du jour : on dégrade
    silencieusement vers « pas de contrainte de dispo ».
    """
    try:
        return load_availability()
    except AvailabilityError:
        return None


def propose_workout_today(
    today: _dt.date | None = None,
    available_min: int | None = None,
) -> dict[str, Any]:
    """Propose une séance pour aujourd'hui.

    Retourne soit :
    - ``{"rest_day": True, "reason": ...}`` quand le weekday du jour n'est pas
      listé dans ``data/availability.yaml`` (et qu'aucun override
      ``available_min`` n'est passé),
    - ``{"rest_day": False, "workout": <Workout.to_dict()>, "tsb": float,
       "tsb_zone": str}`` sinon.

    Paramètres :
    - ``today`` : date à utiliser (par défaut ``date.today()``). Injectable pour
      les tests déterministes.
    - ``available_min`` : durée explicite (override de la dispo du jour). Quand
      passé, l'algo considère que l'utilisateur veut s'entraîner même si
      l'availability le marque off.
    """
    target = today or _dt.date.today()
    weekday = target.weekday()
    availability = _load_availability_safely()

    day = availability.get(weekday) if availability is not None else None
    intervals_day_today = (
        availability is not None
        and availability.intervals_day is not None
        and availability.intervals_day == weekday
    )

    # Jour off (et pas d'override explicite) → on suggère repos.
    if availability is not None and day is None and available_min is None:
        return {
            "rest_day": True,
            "reason": (
                f"{_WEEKDAY_FR[weekday].capitalize()} n'est pas "
                "listé dans ta disponibilité — repos prévu."
            ),
        }

    # CTL/ATL/TSB du jour. Si pas d'activités, TSB = 0 (état neutre).
    activities = fetch_activities_from_db()
    curves = calculate_ctl_atl_tsb(activities, end_date=target)
    tsb = float(curves[-1]["TSB"]) if curves else 0.0

    kind = _kind_for_tsb(tsb, intervals_day_today)
    day_max = day.max_duration_min if day is not None else None
    duration_min = _resolve_duration(kind, day_max, available_min)
    structure = _structure_for(kind, duration_min)
    target_zone = _TARGET_ZONE[kind]
    estimated_tss = round(_TSS_PER_MIN[kind] * duration_min, 1)
    notes = ""
    if day is not None and day.context:
        notes = f"{day.context.capitalize()}"

    workout = Workout(
        date=target.isoformat(),
        name=_name_for(kind, duration_min, week_idx=0, is_taper=False,
                       is_recovery_week=False),
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone=target_zone,
        structure=structure,
        estimated_tss=estimated_tss,
        notes=notes,
    )

    return {
        "rest_day": False,
        "workout": workout.to_dict(),
        "tsb": round(tsb, 1),
        "tsb_zone": _tsb_zone_label(tsb),
    }


__all__ = ["propose_workout_today"]
