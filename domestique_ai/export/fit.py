"""
Export d'un plan d'entraînement en fichiers `.FIT` Garmin Workout.

Chaque `Workout` (cf. domestique_ai.processing.plan_builder) est converti en un
fichier `.FIT` autonome importable manuellement dans Garmin Connect
(Entraînement → Importer un entraînement). Un plan complet est zippé en
`plan_to_zip()` pour un téléchargement unique.

Convention zones HR :
- Si ``hr_rest`` et ``hr_max`` sont fournis, on génère des plages BPM custom via
  Karvonen — cohérent avec ``processing/analyzer._HR_ZONE_BOUNDS``. Le step
  utilise ``target_type=HEART_RATE`` + ``custom_target_heart_rate_low/high`` en
  BPM absolus (la spec FIT considère une valeur ≥ 100 comme BPM).
- Sinon, on retombe sur ``target_hr_zone`` (zones 1..5 configurées sur la montre
  de l'utilisateur).
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from collections.abc import Iterable

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.workout_message import WorkoutMessage
from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage
from fit_tool.profile.profile_type import (
    FileType,
    Intensity,
    Manufacturer,
    Sport,
    WorkoutStepDuration,
    WorkoutStepTarget,
)

from domestique_ai.processing.analyzer import _HR_ZONE_BOUNDS, HR_ZONE_KEYS
from domestique_ai.processing.plan_builder import Workout, WorkoutStep

# Bornes %HRR par zone : (low, high). Z5 va jusqu'à 1.0.
_ZONE_HRR_RANGES: dict[str, tuple[float, float]] = {
    "z1": (0.0, _HR_ZONE_BOUNDS[0]),
    "z2": (_HR_ZONE_BOUNDS[0], _HR_ZONE_BOUNDS[1]),
    "z3": (_HR_ZONE_BOUNDS[1], _HR_ZONE_BOUNDS[2]),
    "z4": (_HR_ZONE_BOUNDS[2], _HR_ZONE_BOUNDS[3]),
    "z5": (_HR_ZONE_BOUNDS[3], 1.0),
}

_PHASE_TO_INTENSITY: dict[str, Intensity] = {
    "warmup": Intensity.WARMUP,
    "active": Intensity.ACTIVE,
    "rest": Intensity.RECOVERY,
    "cooldown": Intensity.COOLDOWN,
}


def _zone_to_bpm_range(zone: str, hr_rest: float, hr_max: float) -> tuple[int, int]:
    """Retourne (low_bpm, high_bpm) pour la zone via Karvonen.

    Pour Z1 on borne le low à 100 bpm minimum (la spec FIT interprète une
    valeur < 100 comme un pourcentage). On élargit aussi très légèrement la
    plage pour qu'elle ne soit pas trivialement vide (low == high).
    """
    if zone not in _ZONE_HRR_RANGES:
        zone = "z2"
    low_pct, high_pct = _ZONE_HRR_RANGES[zone]
    hrr_range = max(1.0, hr_max - hr_rest)
    low = round(hr_rest + low_pct * hrr_range)
    high = round(hr_rest + high_pct * hrr_range)
    low = max(100, int(low))
    high = max(low + 1, int(high))
    return low, high


def _zone_to_garmin_zone_index(zone: str) -> int:
    """Map z1..z5 → 1..5 (zones standard de la montre Garmin)."""
    if zone in HR_ZONE_KEYS:
        return HR_ZONE_KEYS.index(zone) + 1
    return 2


def _build_step(
    step: WorkoutStep, hr_rest: float | None, hr_max: float | None
) -> WorkoutStepMessage:
    """Construit un WorkoutStepMessage depuis notre WorkoutStep."""
    msg = WorkoutStepMessage()
    msg.workout_step_name = f"{step.phase} {step.zone.upper()}"
    msg.intensity = _PHASE_TO_INTENSITY.get(step.phase, Intensity.ACTIVE)
    msg.duration_type = WorkoutStepDuration.TIME
    msg.duration_time = float(step.duration_sec)
    msg.target_type = WorkoutStepTarget.HEART_RATE

    if hr_rest and hr_max and hr_max > hr_rest:
        low_bpm, high_bpm = _zone_to_bpm_range(step.zone, hr_rest, hr_max)
        msg.custom_target_heart_rate_low = low_bpm
        msg.custom_target_heart_rate_high = high_bpm
    else:
        msg.target_hr_zone = _zone_to_garmin_zone_index(step.zone)

    return msg


def workout_to_fit(
    workout: Workout, hr_rest: float | None = None, hr_max: float | None = None
) -> bytes:
    """Sérialise une séance en fichier `.FIT` Garmin Workout (bytes)."""
    file_id = FileIdMessage()
    file_id.type = FileType.WORKOUT
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.time_created = round(_dt.datetime.now(_dt.UTC).timestamp() * 1000)
    file_id.serial_number = 0x12345678

    workout_msg = WorkoutMessage()
    workout_msg.workout_name = workout.name[:50]
    workout_msg.sport = Sport.CYCLING

    steps = [_build_step(s, hr_rest, hr_max) for s in workout.structure]
    workout_msg.num_valid_steps = len(steps)

    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    builder.add(file_id)
    builder.add(workout_msg)
    builder.add_all(steps)
    fit_file = builder.build()
    return bytes(fit_file.to_bytes())


def _safe_filename(name: str) -> str:
    """Slug minimal pour les noms de fichiers dans le ZIP."""
    cleaned = re.sub(r"[^\w\-]+", "_", name, flags=re.ASCII).strip("_")
    return cleaned or "workout"


def plan_to_zip(
    plan: Iterable[Workout], hr_rest: float | None = None, hr_max: float | None = None
) -> bytes:
    """Empaquette un plan complet en archive ZIP (1 `.FIT` par séance)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for workout in plan:
            payload = workout_to_fit(workout, hr_rest=hr_rest, hr_max=hr_max)
            slug = _safe_filename(workout.name)
            filename = f"{workout.date}_{slug}.fit"
            zf.writestr(filename, payload)
    return buffer.getvalue()
