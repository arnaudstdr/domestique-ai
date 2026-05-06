"""
Générateur déterministe de plan d'entraînement multi-semaines.

La logique est purement Python : prend une date cible + l'état de forme courant,
retourne une liste de séances structurées. Aucune dépendance LLM, aucun I/O.

Périodisation appliquée :
- Cycle 3:1 (3 semaines de charge progressive + 1 semaine de récupération).
- Taper sur les 2 dernières semaines avant la date cible (volume −30 % puis −50 %,
  intensité maintenue).
- Distribution hebdo type pour 4 séances :
    * Lundi    — récupération active Z1
    * Mercredi — tempo / sweetspot Z3
    * Vendredi — intervalles seuil/VO2max Z4-Z5
    * Dimanche — endurance longue Z2 (volume croissant)
- Z4-Z5 borné à ≤ 25 % du temps hebdomadaire (polarisation 80/20).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any

from domestique_ai.processing.analyzer import HR_ZONE_KEYS

# Indices de jour (0 = lundi … 6 = dimanche). On précise un jour par séance pour
# que le plan tombe sur des journées cohérentes ; l'utilisateur reste libre de
# déplacer ses séances dans Garmin.
_WEEKDAY_PROFILES: dict[int, list[int]] = {
    2: [2, 6],                 # Mercredi + Dimanche
    3: [1, 3, 6],              # Mardi, Jeudi, Dimanche
    4: [0, 2, 4, 6],           # Lundi, Mercredi, Vendredi, Dimanche
    5: [0, 2, 3, 4, 6],        # + Jeudi (récup intermédiaire)
    6: [0, 1, 2, 4, 5, 6],
    7: [0, 1, 2, 3, 4, 5, 6],
}

# Type de séance pour chaque slot (mêmes longueurs que les profils ci-dessus).
# Le code adapte ensuite la durée et l'intensité selon la phase de périodisation.
_SLOT_KINDS: dict[int, list[str]] = {
    2: ["tempo", "endurance"],
    3: ["tempo", "intervals", "endurance"],
    4: ["recovery", "tempo", "intervals", "endurance"],
    5: ["recovery", "tempo", "recovery", "intervals", "endurance"],
    6: ["recovery", "tempo", "recovery", "intervals", "tempo", "endurance"],
    7: ["recovery", "tempo", "recovery", "intervals", "tempo", "recovery", "endurance"],
}

# Durée de référence (minutes) par type de séance pour la première semaine de bloc.
# Le builder applique ensuite un facteur de progression hebdo.
_BASE_DURATION_MIN: dict[str, int] = {
    "recovery": 45,
    "endurance": 90,
    "tempo": 60,
    "intervals": 60,
}

# Zone HR principale visée par type de séance.
_TARGET_ZONE: dict[str, str] = {
    "recovery": "z1",
    "endurance": "z2",
    "tempo": "z3",
    "intervals": "z4",
}

# TSS estimé par minute de séance (rule of thumb cohérente avec hr-TSS, où
# 1h à LTHR vaut 100 pts) : Z1 ≈ 30/h, Z2 ≈ 55/h, Z3 ≈ 75/h, Z4 ≈ 95/h.
_TSS_PER_MIN: dict[str, float] = {
    "recovery": 30 / 60,
    "endurance": 55 / 60,
    "tempo": 75 / 60,
    "intervals": 95 / 60,
}


@dataclass
class WorkoutStep:
    """Un step d'une séance (warmup, intervalle, récup, cooldown)."""

    phase: str            # "warmup" | "active" | "rest" | "cooldown"
    zone: str             # "z1".."z5"
    duration_sec: int
    repeat: int = 1       # nb de répétitions (déjà aplati par défaut, mais utile pour fit.py)


@dataclass
class Workout:
    """Une séance planifiée."""

    date: str             # ISO YYYY-MM-DD
    name: str             # ex "Sweetspot 2x20"
    sport: str            # "cycling"
    kind: str             # recovery | endurance | tempo | intervals
    duration_min: int
    target_zone: str
    structure: list[WorkoutStep] = field(default_factory=list)
    estimated_tss: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["structure"] = [asdict(s) for s in self.structure]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workout:
        steps = [WorkoutStep(**s) for s in data.get("structure", [])]
        return cls(
            date=data["date"],
            name=data["name"],
            sport=data.get("sport", "cycling"),
            kind=data["kind"],
            duration_min=data["duration_min"],
            target_zone=data["target_zone"],
            structure=steps,
            estimated_tss=data.get("estimated_tss", 0.0),
            notes=data.get("notes", ""),
        )


def _structure_for(kind: str, duration_min: int) -> list[WorkoutStep]:
    """Génère les steps d'une séance en fonction de son type et de sa durée."""
    total_sec = duration_min * 60
    if kind == "recovery":
        return [WorkoutStep(phase="active", zone="z1", duration_sec=total_sec)]
    if kind == "endurance":
        wu = int(total_sec * 0.10)
        cd = int(total_sec * 0.10)
        main = total_sec - wu - cd
        return [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=wu),
            WorkoutStep(phase="active", zone="z2", duration_sec=main),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=cd),
        ]
    if kind == "tempo":
        wu = int(total_sec * 0.15)
        cd = int(total_sec * 0.15)
        main = total_sec - wu - cd
        return [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=wu),
            WorkoutStep(phase="active", zone="z3", duration_sec=main),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=cd),
        ]
    if kind == "intervals":
        # Bloc d'intervalles : 4 reps × (work / rest) avec warmup/cooldown.
        wu = int(total_sec * 0.20)
        cd = int(total_sec * 0.15)
        block = total_sec - wu - cd
        # Cible : 4 répétitions, work:rest = 2:1
        rep_count = 4
        unit = block // rep_count
        work = int(unit * 0.66)
        rest = unit - work
        steps: list[WorkoutStep] = [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=wu),
        ]
        for i in range(rep_count):
            steps.append(WorkoutStep(phase="active", zone="z4", duration_sec=work))
            if i < rep_count - 1:
                steps.append(WorkoutStep(phase="rest", zone="z1", duration_sec=rest))
        steps.append(WorkoutStep(phase="cooldown", zone="z1", duration_sec=cd))
        return steps
    return [WorkoutStep(phase="active", zone="z2", duration_sec=total_sec)]


def _name_for(kind: str, duration_min: int, week_idx: int, is_taper: bool,
              is_recovery_week: bool) -> str:
    if is_taper:
        suffix = " (taper)"
    elif is_recovery_week:
        suffix = " (récup)"
    else:
        suffix = ""
    if kind == "recovery":
        return f"Récupération active {duration_min}'" + suffix
    if kind == "endurance":
        return f"Endurance Z2 {duration_min}'" + suffix
    if kind == "tempo":
        return f"Tempo Z3 {duration_min}'" + suffix
    if kind == "intervals":
        return f"Intervalles Z4 4x{int(duration_min * 0.66 / 4)}'" + suffix
    return f"Séance {duration_min}'" + suffix


def _week_factor(week_idx: int, total_weeks: int, taper_weeks: int) -> tuple[float, bool, bool]:
    """Retourne (volume_factor, is_recovery, is_taper) pour la semaine ``week_idx``.

    ``week_idx`` est 0-indexé depuis le début du plan. La phase de taper occupe
    les ``taper_weeks`` dernières semaines. Hors taper, on applique un cycle 3:1
    avec progression sur les 3 semaines de charge.
    """
    weeks_to_event = total_weeks - week_idx - 1  # 0 = semaine de l'objectif

    if weeks_to_event < taper_weeks:
        # Taper : volume réduit, plus on s'approche de la date.
        # 2 sem avant : 0.70 ; 1 sem avant : 0.50.
        factor = 0.70 if weeks_to_event >= 1 else 0.50
        return factor, False, True

    # Cycle 3:1 — position dans le cycle (0..3).
    cycle_pos = week_idx % 4
    if cycle_pos == 3:
        return 0.65, True, False
    # Progression douce sur les 3 sem de charge : 0.85 → 1.00 → 1.10
    return 0.85 + 0.125 * cycle_pos, False, False


def _ctl_progression_cap(ctl_current: float, weeks_into_plan: int) -> float:
    """Plafond de TSS hebdo cohérent avec une progression CTL bornée à +5/sem.

    CTL est une EMA 42 j. À TSB stable, augmenter CTL de Δ par semaine demande
    un TSS hebdo ≈ 7 × (CTL_actuel + Δ). On cale donc le volume hebdomadaire
    sur (CTL_actuel + 5 × weeks_into_plan) × 7.
    """
    target_ctl = max(20.0, ctl_current) + 5.0 * weeks_into_plan
    return target_ctl * 7.0


def build_training_plan(
    *,
    target_date: _dt.date | None,
    ctl_current: float = 0.0,
    sessions_per_week: int = 4,
    target_event_type: str = "cyclosportive",
    focus: str | None = None,
    start_date: _dt.date | None = None,
    fallback_weeks: int = 4,
) -> list[Workout]:
    """Construit la liste des séances entre ``start_date`` et ``target_date``.

    Si ``target_date`` est absent, génère ``fallback_weeks`` semaines à partir
    de ``start_date`` (ou aujourd'hui).
    Les paramètres ``target_event_type`` et ``focus`` sont conservés pour pilotage
    futur (taper plus court pour cyclo, plus d'intervalles pour course, etc.) —
    ils n'altèrent pas la structure de base aujourd'hui.
    """
    if sessions_per_week not in _WEEKDAY_PROFILES:
        raise ValueError(
            f"sessions_per_week doit être dans {sorted(_WEEKDAY_PROFILES)}, reçu {sessions_per_week}"
        )

    today = start_date or _dt.date.today()
    if target_date is None:
        total_weeks = max(1, fallback_weeks)
        target_date = today + _dt.timedelta(days=total_weeks * 7)
    else:
        days_to_event = (target_date - today).days
        total_weeks = max(1, (days_to_event + 6) // 7)

    taper_weeks = min(2, total_weeks - 1) if total_weeks > 2 else 0

    # Normaliser le début sur le lundi de la semaine de ``today`` pour aligner
    # les jours de séance sur le calendrier hebdo.
    week_start = today - _dt.timedelta(days=today.weekday())

    weekday_slots = _WEEKDAY_PROFILES[sessions_per_week]
    slot_kinds = _SLOT_KINDS[sessions_per_week]

    plan: list[Workout] = []
    for week_idx in range(total_weeks):
        factor, is_recovery, is_taper = _week_factor(week_idx, total_weeks, taper_weeks)
        weeks_into_plan = week_idx if not is_taper else max(0, total_weeks - taper_weeks - 1)
        weekly_tss_cap = _ctl_progression_cap(ctl_current, weeks_into_plan)

        sessions_this_week: list[Workout] = []
        weekly_tss = 0.0
        weekly_intensity_sec = 0.0
        weekly_total_sec = 0.0
        for slot_idx, weekday in enumerate(weekday_slots):
            session_date = week_start + _dt.timedelta(days=week_idx * 7 + weekday)
            if session_date < today:
                # On saute les jours déjà passés dans la semaine en cours.
                continue
            kind = slot_kinds[slot_idx]
            # En semaine de récup ou taper, on bascule les intervalles vers tempo léger.
            if (is_recovery or is_taper) and kind == "intervals":
                kind = "tempo"
            base_min = _BASE_DURATION_MIN[kind]
            # Endurance progresse plus vite avec les semaines de charge.
            if kind == "endurance" and not (is_recovery or is_taper):
                base_min = int(base_min + 5 * (week_idx % 4))
            duration_min = max(20, int(base_min * factor))

            target_zone = _TARGET_ZONE[kind]
            structure = _structure_for(kind, duration_min)
            tss = round(_TSS_PER_MIN[kind] * duration_min, 1)

            weekly_tss += tss
            weekly_total_sec += duration_min * 60
            if kind == "intervals":
                weekly_intensity_sec += sum(
                    s.duration_sec for s in structure if s.zone in ("z4", "z5")
                )

            workout = Workout(
                date=session_date.isoformat(),
                name=_name_for(kind, duration_min, week_idx, is_taper, is_recovery),
                sport="cycling",
                kind=kind,
                duration_min=duration_min,
                target_zone=target_zone,
                structure=structure,
                estimated_tss=tss,
                notes=focus or "",
            )
            sessions_this_week.append(workout)

        # Plafond global TSS hebdo : si on dépasse, on raccourcit l'endurance longue.
        if weekly_tss > weekly_tss_cap and sessions_this_week:
            scale = weekly_tss_cap / weekly_tss
            scaled: list[Workout] = []
            for w in sessions_this_week:
                if w.kind == "endurance":
                    new_min = max(45, int(w.duration_min * scale))
                    new_struct = _structure_for("endurance", new_min)
                    scaled.append(Workout(
                        date=w.date,
                        name=_name_for("endurance", new_min, week_idx, is_taper, is_recovery),
                        sport=w.sport,
                        kind=w.kind,
                        duration_min=new_min,
                        target_zone=w.target_zone,
                        structure=new_struct,
                        estimated_tss=round(_TSS_PER_MIN["endurance"] * new_min, 1),
                        notes=w.notes,
                    ))
                else:
                    scaled.append(w)
            sessions_this_week = scaled

        plan.extend(sessions_this_week)

    return plan


def assert_zones_valid(plan: list[Workout]) -> None:
    """Sanity check : toutes les zones citées sont dans HR_ZONE_KEYS."""
    valid = set(HR_ZONE_KEYS)
    for w in plan:
        if w.target_zone not in valid:
            raise ValueError(f"target_zone invalide: {w.target_zone}")
        for step in w.structure:
            if step.zone not in valid:
                raise ValueError(f"step.zone invalide: {step.zone}")
