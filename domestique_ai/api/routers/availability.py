"""Lecture / écriture de la disponibilité hebdomadaire (``data/availability.yaml``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from domestique_ai.api.schemas import (
    AvailabilityPreferencesSchema,
    AvailabilitySchema,
    DayAvailabilityIn,
)
from domestique_ai.llm.availability import (
    _WEEKDAY_BY_INDEX,
    _WEEKDAY_NAMES,
    Availability,
    AvailabilityError,
    DayAvailability,
    load_availability,
    save_availability,
)

router = APIRouter(prefix="/api/availability", tags=["availability"])


def _model_to_schema(model: Availability) -> AvailabilitySchema:
    days: dict[str, DayAvailabilityIn] = {}
    for d in sorted(model.days, key=lambda x: x.weekday):
        days[_WEEKDAY_BY_INDEX[d.weekday]] = DayAvailabilityIn(
            max_duration_min=d.max_duration_min,
            context=d.context,  # type: ignore[arg-type]
        )
    prefs: AvailabilityPreferencesSchema | None = None
    if model.long_endurance_day is not None or model.intervals_day is not None:
        prefs = AvailabilityPreferencesSchema(
            long_endurance_day=(
                _WEEKDAY_BY_INDEX[model.long_endurance_day]
                if model.long_endurance_day is not None
                else None
            ),
            intervals_day=(
                _WEEKDAY_BY_INDEX[model.intervals_day]
                if model.intervals_day is not None
                else None
            ),
        )
    return AvailabilitySchema(days=days, preferences=prefs)


def _schema_to_model(payload: AvailabilitySchema) -> Availability:
    days: list[DayAvailability] = []
    for name, raw in payload.days.items():
        key = name.strip().lower()
        if key not in _WEEKDAY_NAMES:
            raise AvailabilityError(
                f"Nom de jour invalide: {name!r}. Attendu: {sorted(_WEEKDAY_NAMES)}"
            )
        days.append(
            DayAvailability(
                weekday=_WEEKDAY_NAMES[key],
                max_duration_min=raw.max_duration_min,
                context=raw.context,
            )
        )
    days.sort(key=lambda d: d.weekday)

    long_endurance: int | None = None
    intervals: int | None = None
    if payload.preferences is not None:
        known = {d.weekday for d in days}
        if payload.preferences.long_endurance_day:
            wd = _WEEKDAY_NAMES.get(
                payload.preferences.long_endurance_day.strip().lower()
            )
            if wd in known:
                long_endurance = wd
        if payload.preferences.intervals_day:
            wd = _WEEKDAY_NAMES.get(
                payload.preferences.intervals_day.strip().lower()
            )
            if wd in known:
                intervals = wd

    return Availability(
        days=days,
        long_endurance_day=long_endurance,
        intervals_day=intervals,
    )


@router.get("", response_model=AvailabilitySchema | None)
def get_availability() -> AvailabilitySchema | None:
    """Renvoie la disponibilité persistée ou ``null`` si absente."""
    av = load_availability()
    if av is None:
        return None
    return _model_to_schema(av)


@router.put("", response_model=AvailabilitySchema)
def put_availability(payload: AvailabilitySchema) -> AvailabilitySchema:
    """Remplace la disponibilité hebdomadaire (réécrit ``data/availability.yaml``)."""
    try:
        model = _schema_to_model(payload)
        save_availability(model)
    except AvailabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    # On renvoie la version normalisée (re-sérialisée → re-désérialisée) pour
    # ne pas laisser de doute côté front sur la forme finale.
    return _model_to_schema(model)
