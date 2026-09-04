"""
Validation et correction déterministe d'un plan d'entraînement.

Ce module est le **garde-fou** de la génération de plans par LLM : il prend
une liste de ``Workout`` (potentiellement produite par un modèle) et applique
des règles physiologiques strictes pour ramener le plan dans des bornes
sûres et cohérentes.

Règles appliquées, dans cet ordre, semaine par semaine :

1. **Disponibilité** : les séances tombant un jour non disponible (selon
   ``availability.yaml``) sont supprimées. Les séances dont la durée dépasse
   le ``max_duration_min`` du jour sont raccourcies.
2. **Repos hebdomadaire** : au moins 1 jour sans séance par semaine. Si la
   semaine LLM contient 7 séances, on supprime celle de plus faible priorité
   (récup > tempo > intervals > endurance long).
3. **Polarisation 80/20** : la part Z4-Z5 ne doit pas dépasser 25 % du temps
   actif hebdo. Au-dessus, on convertit progressivement les ``intervals`` en
   ``tempo`` jusqu'à respecter la borne.
4. **Plafond TSS hebdo** : ne pas dépasser le cap ``_ctl_progression_cap`` qui
   borne la progression de CTL à +5 points par semaine. En cas de
   dépassement, on raccourcit l'endurance (la sortie longue garde un plancher
   plus haut, 60 min).
5. **Cadence d'intensité par type d'objectif** : sur les semaines de charge,
   le plan doit contenir une séance d'intensité conforme au type (``intervals``
   chaque semaine pour course/cyclosportive/maintenance ; ``intervals`` une
   semaine sur deux et tempo sinon pour cyclo/forme). Conversion de l'endurance
   la plus longue, uniquement si le plafond TSS le permet.

À chaque correction appliquée, on émet une chaîne descriptive dans la liste
``adjustments`` retournée, pour que l'UI puisse afficher un badge « ajusté »
sur les séances impactées.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from domestique_ai.llm.availability import Availability
from domestique_ai.processing.plan_builder import (
    _BASE_DURATION_MIN,
    _TARGET_ZONE,
    _TSS_PER_MIN,
    Workout,
    _ctl_progression_cap,
    _name_for,
    _structure_for,
)

# Part Z4-Z5 maximale du temps actif hebdomadaire (polarisation 80/20).
_MAX_HIGH_INTENSITY_SHARE = 0.25

# Nombre maximal de séances par semaine (au moins 1 jour de repos).
_MAX_SESSIONS_PER_WEEK = 6

# Priorité de conservation lors de la coupure d'une séance excédentaire.
# La plus basse = la première coupée.
_KIND_PRIORITY: dict[str, int] = {
    "recovery": 0,
    "tempo": 1,
    "intervals": 2,
    "endurance": 3,
}


def _iso_week_key(date_iso: str) -> tuple[int, int]:
    """``"2026-05-25" -> (2026, 22)`` (clé hebdomadaire ISO 8601)."""
    iso_year, iso_week, _ = _dt.date.fromisoformat(date_iso).isocalendar()
    return iso_year, iso_week


def _group_by_week(plan: list[Workout]) -> dict[tuple[int, int], list[Workout]]:
    """Regroupe les séances par semaine ISO, triées chronologiquement."""
    by_week: dict[tuple[int, int], list[Workout]] = defaultdict(list)
    for workout in plan:
        by_week[_iso_week_key(workout.date)].append(workout)
    for week in by_week.values():
        week.sort(key=lambda w: w.date)
    return by_week


def _high_intensity_seconds(workout: Workout) -> int:
    """Temps cumulé en Z4-Z5 pour une séance, basé sur les steps actifs."""
    total = 0
    for step in workout.structure:
        if step.phase == "active" and step.zone in {"z4", "z5"}:
            total += step.duration_sec
    return total


def _active_seconds(workout: Workout) -> int:
    """Temps actif (hors phases ``rest``)."""
    return sum(step.duration_sec for step in workout.structure if step.phase != "rest")


def _rebuild_workout(
    workout: Workout,
    new_kind: str,
    new_duration_min: int,
    week_idx: int,
    is_recovery_week: bool = False,
    is_taper: bool = False,
) -> Workout:
    """Reconstruit une séance après changement de type ou de durée.

    On régénère la structure via ``_structure_for`` pour rester cohérent avec
    le pattern du builder déterministe (warmup/active/cooldown, ratios fixes).
    """
    new_duration_min = max(20, new_duration_min)
    return Workout(
        date=workout.date,
        name=_name_for(new_kind, new_duration_min, week_idx, is_taper, is_recovery_week),
        sport=workout.sport,
        kind=new_kind,
        duration_min=new_duration_min,
        target_zone=_TARGET_ZONE.get(new_kind, "z2"),
        structure=_structure_for(new_kind, new_duration_min),
        estimated_tss=round(_TSS_PER_MIN.get(new_kind, 1.0) * new_duration_min, 1),
        notes=workout.notes,
        uid=workout.uid,
    )


def _enforce_availability(
    week: list[Workout],
    availability: Availability | None,
    adjustments: list[str],
) -> list[Workout]:
    """Supprime les séances hors jours dispos, plafonne les durées au max."""
    if availability is None:
        return week
    allowed = {d.weekday: d for d in availability.days}
    out: list[Workout] = []
    for w in week:
        weekday = _dt.date.fromisoformat(w.date).weekday()
        day = allowed.get(weekday)
        if day is None:
            adjustments.append(f"{w.date} : séance retirée (jour hors disponibilité)")
            continue
        if w.duration_min > day.max_duration_min:
            new_min = max(20, day.max_duration_min)
            adjustments.append(
                f"{w.date} : durée plafonnée à {new_min} min (dispo = {day.max_duration_min})"
            )
            week_idx = _weeks_since_plan_start(w.date, week[0].date)
            out.append(_rebuild_workout(w, w.kind, new_min, week_idx))
        else:
            out.append(w)
    return out


def _enforce_rest_day(
    week: list[Workout],
    adjustments: list[str],
) -> list[Workout]:
    """Garantit au moins 1 jour de repos par semaine (max 6 séances)."""
    if len(week) <= _MAX_SESSIONS_PER_WEEK:
        return week
    # Couper la séance de plus basse priorité (récup d'abord, puis tempo, …).
    week_sorted = sorted(
        enumerate(week),
        key=lambda iw: (_KIND_PRIORITY.get(iw[1].kind, 5), iw[1].date),
    )
    surplus = len(week) - _MAX_SESSIONS_PER_WEEK
    to_drop = {idx for idx, _ in week_sorted[:surplus]}
    for idx in to_drop:
        adjustments.append(
            f"{week[idx].date} : séance retirée "
            f"(repos hebdo, {len(week)} séances > {_MAX_SESSIONS_PER_WEEK})"
        )
    return [w for i, w in enumerate(week) if i not in to_drop]


def _enforce_polarization(
    week: list[Workout],
    week_idx: int,
    adjustments: list[str],
) -> list[Workout]:
    """Borne la part Z4-Z5 à 25 % du temps actif hebdo.

    Au-dessus, on convertit les ``intervals`` en ``tempo`` un par un (du moins
    important vers le plus important : on commence par le plus court).
    """
    high = sum(_high_intensity_seconds(w) for w in week)
    active = sum(_active_seconds(w) for w in week)
    if active == 0 or high / active <= _MAX_HIGH_INTENSITY_SHARE:
        return week

    # Candidats à convertir : séances Z4-Z5 triées du plus court au plus long.
    candidates = sorted(
        [
            (i, w)
            for i, w in enumerate(week)
            if w.kind == "intervals" or _high_intensity_seconds(w) > 0
        ],
        key=lambda iw: _high_intensity_seconds(iw[1]),
    )
    new_week = list(week)
    for idx, candidate in candidates:
        if candidate.kind == "intervals":
            new_week[idx] = _rebuild_workout(candidate, "tempo", candidate.duration_min, week_idx)
            adjustments.append(f"{candidate.date} : intervals → tempo (polarisation 80/20)")
            high = sum(_high_intensity_seconds(w) for w in new_week)
            active = sum(_active_seconds(w) for w in new_week)
            if active == 0 or high / active <= _MAX_HIGH_INTENSITY_SHARE:
                return new_week
    return new_week


def _enforce_tss_cap(
    week: list[Workout],
    week_idx: int,
    ctl_current: float,
    adjustments: list[str],
    min_ctl: float = 20.0,
) -> list[Workout]:
    """Borne le TSS hebdo au plafond de progression CTL (+5 / semaine)."""
    cap = _ctl_progression_cap(ctl_current, week_idx, min_ctl)
    total = sum(w.estimated_tss for w in week)
    if total <= cap:
        return week

    # Raccourcir l'endurance la plus longue d'abord, en préservant un plancher
    # plus haut pour la sortie longue (60 min) que pour les autres (45 min) —
    # la semaine garde une séance structurante même sous plafond.
    endurance = sorted(
        [(i, w) for i, w in enumerate(week) if w.kind == "endurance"],
        key=lambda iw: -iw[1].duration_min,
    )
    new_week = list(week)
    for rank, (idx, candidate) in enumerate(endurance):
        # On vise un new_min tel que la somme passe sous le cap.
        excess = total - cap
        excess_min = excess / max(_TSS_PER_MIN["endurance"], 1e-6)
        floor = 60 if rank == 0 else 45
        new_min = max(floor, int(candidate.duration_min - excess_min))
        if new_min == candidate.duration_min:
            continue
        new_week[idx] = _rebuild_workout(candidate, "endurance", new_min, week_idx)
        adjustments.append(
            f"{candidate.date} : endurance raccourcie à {new_min} min (plafond TSS hebdo)"
        )
        total = sum(w.estimated_tss for w in new_week)
        if total <= cap:
            return new_week
    return new_week


def _enforce_intensity_cadence(
    week: list[Workout],
    week_idx: int,
    ctl_current: float,
    flavor: dict[str, float],
    total_weeks: int | None,
    min_ctl: float,
    adjustments: list[str],
) -> list[Workout]:
    """Garantit la cadence d'intensité du type d'objectif, sans dépasser le plafond.

    Sur les semaines de charge (ni récupération 3:1, ni taper) :
    - type à ``intervals_freq=1`` (course/cyclosportive/maintenance) : chaque
      semaine attend une séance d'``intervals`` ;
    - type à ``intervals_freq=2`` (cyclo/forme) : une semaine sur deux attend
      ``intervals``, l'autre au moins un ``tempo``.

    Si la séance attendue manque, on convertit l'endurance la plus longue —
    uniquement si la conversion reste dans le plafond TSS (une intensité de
    25 min ne vaut rien, on laisse la semaine telle quelle plutôt que de
    casser la structure).
    """
    taper_weeks = int(flavor["taper_weeks"])
    if total_weeks is not None and week_idx >= total_weeks - taper_weeks:
        return week
    if week_idx % 4 == 3:
        return week  # semaine de récupération du cycle 3:1
    # Une « vraie » semaine d'entraînement structurée (≥ 3 séances) : on ne
    # force pas une séance isolée (sortie longue, jour unique) en intensité.
    if len(week) < 3:
        return week

    intervals_week = (week_idx % int(flavor["intervals_freq"])) == 0
    has_intervals = any(w.kind == "intervals" for w in week)
    has_tempo = any(w.kind == "tempo" for w in week)
    if intervals_week and has_intervals:
        return week
    if not intervals_week and (has_tempo or has_intervals):
        return week

    target_kind = "intervals" if intervals_week else "tempo"
    candidates = sorted(
        [(i, w) for i, w in enumerate(week) if w.kind == "endurance"],
        key=lambda iw: -iw[1].duration_min,
    )
    if not candidates:
        return week
    idx, candidate = candidates[0]

    # Taille maximale qui reste dans le plafond (conversion TSS-équivalente).
    cap = _ctl_progression_cap(ctl_current, week_idx, min_ctl)
    week_tss = sum(w.estimated_tss for w in week)
    room = cap - (week_tss - (candidate.estimated_tss or 0.0))
    max_dur = int(room / max(_TSS_PER_MIN[target_kind], 1e-6))
    max_dur = min(max_dur, int(_BASE_DURATION_MIN[target_kind]))
    new_min = max(20, min(candidate.duration_min, max_dur))
    if new_min < 20 or new_min == candidate.duration_min and target_kind == "tempo":
        return week  # pas de place pour une intensité utile → on laisse
    new_week = list(week)
    new_week[idx] = _rebuild_workout(candidate, target_kind, new_min, week_idx)
    adjustments.append(
        f"{candidate.date} : {candidate.kind} → {target_kind} "
        f"({new_min} min, cadence {int(flavor['intervals_freq'])}/1)"
    )
    return new_week


def _weeks_since_plan_start(date_iso: str, plan_start_iso: str) -> int:
    """Indice 0-based de la semaine d'une séance par rapport au début du plan."""
    start_year, start_week = _iso_week_key(plan_start_iso)
    cur_year, cur_week = _iso_week_key(date_iso)
    return (cur_year - start_year) * 52 + (cur_week - start_week)


def validate_and_correct(
    plan: list[Workout],
    *,
    ctl_current: float = 0.0,
    availability: Availability | None = None,
    target_event_type: str = "cyclosportive",
    total_weeks: int | None = None,
    min_ctl: float = 20.0,
) -> tuple[list[Workout], list[str]]:
    """Applique les garde-fous au plan et retourne ``(plan_corrigé, ajustements)``.

    Args:
        plan : liste des séances proposées (généralement par un LLM).
        ctl_current : CTL en cours, sert au calcul du plafond TSS hebdo.
        availability : disponibilité hebdomadaire si présente (sinon les
            règles de durée/jour ne s'appliquent pas).
        target_event_type : type d'objectif — pilote la cadence d'intensité
            (intervalles chaque semaine vs une semaine sur deux).
        total_weeks : nombre de semaines du plan (pour exclure les semaines de
            taper de la cadence d'intensité). ``None`` = inconnu (traité comme
            semaines de charge).
        min_ctl : plancher du plafond TSS hebdo (voir ``_ctl_progression_cap``).

    Returns:
        Un tuple ``(plan, adjustments)`` où ``plan`` est l'ensemble corrigé et
        ``adjustments`` est une liste de messages décrivant chaque modification
        (utilisée pour afficher un badge « ajusté » côté UI).
    """
    if not plan:
        return [], []

    from domestique_ai.processing.plan_builder import _objective_flavor

    adjustments: list[str] = []
    by_week = _group_by_week(plan)
    plan_start_iso = min(w.date for w in plan)
    flavor = _objective_flavor(target_event_type)

    corrected: list[Workout] = []
    for week_key in sorted(by_week):
        week = by_week[week_key]
        week_idx = _weeks_since_plan_start(week[0].date, plan_start_iso)

        week = _enforce_availability(week, availability, adjustments)
        if not week:
            continue
        week = _enforce_rest_day(week, adjustments)
        week = _enforce_polarization(week, week_idx, adjustments)
        week = _enforce_tss_cap(week, week_idx, ctl_current, adjustments, min_ctl)
        week = _enforce_intensity_cadence(
            week, week_idx, ctl_current, flavor, total_weeks, min_ctl, adjustments
        )
        corrected.extend(week)

    return corrected, adjustments


# ---------- Helpers exposés pour les tests / autres modules -----------------


def weekly_tss(plan: list[Workout]) -> dict[tuple[int, int], float]:
    """Somme du TSS estimé par semaine ISO."""
    out: dict[tuple[int, int], float] = defaultdict(float)
    for w in plan:
        out[_iso_week_key(w.date)] += w.estimated_tss
    return dict(out)


def weekly_high_intensity_share(plan: list[Workout]) -> dict[tuple[int, int], float]:
    """Part Z4-Z5 du temps actif hebdo (entre 0 et 1)."""
    high: dict[tuple[int, int], int] = defaultdict(int)
    active: dict[tuple[int, int], int] = defaultdict(int)
    for w in plan:
        k = _iso_week_key(w.date)
        high[k] += _high_intensity_seconds(w)
        active[k] += _active_seconds(w)
    return {k: (high[k] / active[k] if active[k] > 0 else 0.0) for k in active}


def expected_default_duration(kind: str) -> int:
    """Durée de référence par type de séance (utilisée par les tests)."""
    return _BASE_DURATION_MIN.get(kind, 60)


__all__ = [
    "expected_default_duration",
    "validate_and_correct",
    "weekly_high_intensity_share",
    "weekly_tss",
]


# Faire référence au paramètre pour éviter le warning Ruff sur l'import non
# utilisé : on l'expose pour la cohérence d'API.
_ = Any
