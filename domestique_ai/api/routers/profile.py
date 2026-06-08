"""Lecture / écriture du profil utilisateur (``data/profile.yaml``)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import ProfileSchema
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.config import invalidate_profile_cache
from domestique_ai.llm.profile import (
    Profile as ProfileModel,
)
from domestique_ai.llm.profile import (
    ProfileError,
    load_profile,
    save_profile,
)
from domestique_ai.processing.analyzer import recalculate_training_loads

router = APIRouter(prefix="/api/profile", tags=["profile"])
log = get_logger("profile")


def _to_schema(model: ProfileModel) -> ProfileSchema:
    return ProfileSchema(
        ftp=model.ftp,
        hr_rest=model.hr_rest,
        hr_max=model.hr_max,
        sex=model.sex,  # type: ignore[arg-type]
        lthr_pct=model.lthr_pct,
    )


def _hr_relevant_fields_changed(
    previous: ProfileModel | None, new: ProfileSchema
) -> bool:
    """Vrai si l'un des paramètres qui influencent le hr-TSS a changé.

    hr_rest, hr_max, sex et lthr_pct entrent dans le calcul de
    ``compute_training_load`` quand on est en mode hr-TSS. Une modif de ces
    champs invalide les valeurs persistées en base.
    """
    if previous is None:
        # Aucun profil avant : si on en pose un avec des valeurs HR, on recalcule.
        return any(
            v is not None for v in (new.hr_rest, new.hr_max)
        ) or new.lthr_pct != 0.88 or new.sex != "M"
    return (
        previous.hr_rest != new.hr_rest
        or previous.hr_max != new.hr_max
        or previous.sex != new.sex
        or previous.lthr_pct != new.lthr_pct
    )


@router.get("", response_model=ProfileSchema | None)
def get_profile(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ProfileSchema | None:
    """Renvoie le profil persisté ou ``null`` si absent."""
    profile = load_profile(ctx.profile_path)
    if profile is None:
        return None
    return _to_schema(profile)


@router.put("", response_model=ProfileSchema)
def put_profile(
    payload: ProfileSchema,
    background_tasks: BackgroundTasks,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> ProfileSchema:
    """Remplace le profil. Si HR/sexe/%LTHR a changé, recalcule la charge en tâche de fond."""
    previous = load_profile(ctx.profile_path)
    try:
        save_profile(
            ProfileModel(
                ftp=payload.ftp,
                hr_rest=payload.hr_rest,
                hr_max=payload.hr_max,
                sex=payload.sex,
                lthr_pct=payload.lthr_pct,
            ),
            ctx.profile_path,
        )
    except ProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Invalide le cache config (utile pour le bootstrap qui passe par lui ;
    # no-op sémantique pour les athlètes dont le profil est lu hors cache).
    invalidate_profile_cache()

    if _hr_relevant_fields_changed(previous, payload):
        log.info(
            "Profil HR modifié — recalcul training_load lancé en arrière-plan."
        )
        background_tasks.add_task(recalculate_training_loads, ctx=ctx)

    return payload
