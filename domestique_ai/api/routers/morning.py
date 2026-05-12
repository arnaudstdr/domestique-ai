"""Endpoints des métriques matinales (HRV, FC repos, sommeil, stress)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, status
from fastapi.responses import Response

from domestique_ai.api.schemas import (
    MorningAlert,
    MorningBaseline,
    MorningEntry,
    MorningResponse,
    MorningSubmit,
)
from domestique_ai.processing.morning_metrics import (
    METRIC_COLUMNS,
    compute_baselines,
    detect_morning_alerts,
    fetch_morning_history,
    save_morning_entry,
)

router = APIRouter(prefix="/api/morning", tags=["morning"])


@router.get("", response_model=MorningResponse)
def get_morning(days: int = 90) -> MorningResponse:
    """Historique sur N jours + baselines 14 j + alertes de dérive."""
    history = fetch_morning_history(days=days)
    baselines: dict[str, MorningBaseline] = {}
    for metric in METRIC_COLUMNS:
        b = compute_baselines(metric)
        baselines[metric] = MorningBaseline(
            available=b.get("available", False),
            metric=metric,
            baseline=b.get("baseline"),
            latest=b.get("latest"),
            latest_date=b.get("latest_date"),
            delta_pct=b.get("delta_pct"),
            sample_size=b.get("sample_size"),
            reason=b.get("reason"),
        )

    alerts = [
        MorningAlert(
            metric=a["metric"],
            delta_pct=a["delta_pct"],
            baseline=a["baseline"],
            latest=a["latest"],
            latest_date=a["latest_date"],
            severity=a["severity"],
        )
        for a in detect_morning_alerts()
    ]

    return MorningResponse(
        history=[MorningEntry(**e) for e in history],
        baselines=baselines,
        alerts=alerts,
    )


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def post_morning(payload: MorningSubmit) -> Response:
    """Enregistre (ou remplace, idempotent sur la date) une entrée matinale."""
    target_date = payload.date or dt.date.today().isoformat()
    save_morning_entry(
        target_date,
        hrv_ms=payload.hrv_ms,
        resting_hr=payload.resting_hr,
        sleep_hours=payload.sleep_hours,
        sleep_score=payload.sleep_score,
        stress_score=payload.stress_score,
        notes=payload.notes,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
