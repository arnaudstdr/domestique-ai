"""Lecture des séances prescrites — vue de l'athlète courant.

GET scopé par ``get_athlete_context`` : sert l'athlète sur ses propres données et
le coach en consultation (impersonation ``?athlete=`` — GET autorisé). L'écriture
(création/suppression) passe par le routeur coach ``/api/roster``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.schemas import PrescriptionOut
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.llm.prescription_storage import list_prescriptions

router = APIRouter(prefix="/api/prescriptions", tags=["prescriptions"])


@router.get("", response_model=list[PrescriptionOut])
def get_prescriptions(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> list[PrescriptionOut]:
    """Liste les séances prescrites à l'athlète (par date croissante)."""
    return [PrescriptionOut(**row) for row in list_prescriptions(db_path=ctx.db_path)]
