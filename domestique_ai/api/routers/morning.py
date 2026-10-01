"""Endpoints des métriques matinales (HRV, FC repos, sommeil, stress)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.schemas import (
    HealthProviderStatus,
    HealthProviderUpdate,
    HealthSourcesResponse,
    MorningAlert,
    MorningBaseline,
    MorningEntry,
    MorningResponse,
    MorningSubmit,
    WeightResponse,
    WeightSubmit,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.processing.morning_metrics import (
    METRIC_COLUMNS,
    compute_baselines,
    detect_morning_alerts,
    fetch_morning_history,
    get_health_provider,
    latest_weight_entry,
    power_to_weight,
    resolve_health_provider,
    save_morning_entry,
    set_health_provider,
    set_weight,
)

router = APIRouter(prefix="/api/morning", tags=["morning"])


@router.get("", response_model=MorningResponse)
def get_morning(
    days: int = 90,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> MorningResponse:
    """Historique sur N jours + baselines 14 j + alertes de dérive."""
    history = fetch_morning_history(days=days, db_path=ctx.db_path)
    baselines: dict[str, MorningBaseline] = {}
    for metric in METRIC_COLUMNS:
        b = compute_baselines(metric, db_path=ctx.db_path)
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
        for a in detect_morning_alerts(db_path=ctx.db_path)
    ]

    return MorningResponse(
        history=[MorningEntry(**e) for e in history],
        baselines=baselines,
        alerts=alerts,
    )


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def post_morning(
    payload: MorningSubmit,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
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
        spo2_avg_pct=payload.spo2_avg_pct,
        respiratory_rate_avg_bpm=payload.respiratory_rate_avg_bpm,
        skin_temp_delta_c=payload.skin_temp_delta_c,
        sleep_deep_min=payload.sleep_deep_min,
        sleep_rem_min=payload.sleep_rem_min,
        sleep_light_min=payload.sleep_light_min,
        sleep_awake_min=payload.sleep_awake_min,
        steps=payload.steps,
        active_calories=payload.active_calories,
        readiness_score=payload.readiness_score,
        # Si l'utilisateur saisit un sleep_score manuel, on le marque comme tel
        # pour ne pas l'écraser lors du prochain sync Google Health.
        sleep_score_computed=0 if payload.sleep_score is not None else None,
        stress_score_computed=0 if payload.stress_score is not None else None,
        weight_kg=payload.weight_kg,
        db_path=ctx.db_path,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _garmin_provider_status(ctx: AthleteContext) -> HealthProviderStatus:
    """État de la connexion Garmin + fraîcheur de la dernière sync santé."""
    from domestique_ai.config import garmin_token_dir_for
    from domestique_ai.export.garmin_connect import credentials_present, token_cache_present
    from domestique_ai.ingestion.db import get_sync_meta
    from domestique_ai.ingestion.garmin_health import (
        GARMIN_HEALTH_LAST_ERROR_KEY,
        GARMIN_HEALTH_LAST_SYNC_KEY,
    )

    configured = credentials_present(ctx.garmin_email, ctx.garmin_password)
    connected = configured and token_cache_present(garmin_token_dir_for(ctx))
    return HealthProviderStatus(
        configured=configured,
        connected=connected,
        last_sync_at=get_sync_meta(GARMIN_HEALTH_LAST_SYNC_KEY, db_path=ctx.db_path),
        last_error=get_sync_meta(GARMIN_HEALTH_LAST_ERROR_KEY, db_path=ctx.db_path) or None,
    )


def _google_provider_status(ctx: AthleteContext) -> HealthProviderStatus:
    """État de la connexion Google Health + fraîcheur de la dernière sync."""
    from domestique_ai.config import get_google_health_credentials, google_health_tokens_path_for
    from domestique_ai.ingestion.google_health import GoogleHealthClient

    client_id, client_secret, _ = get_google_health_credentials()
    configured = bool(client_id and client_secret)
    client = GoogleHealthClient.from_tokens_file(google_health_tokens_path_for(ctx))
    connected = client is not None and client.is_authenticated()
    last_sync_at = None
    if client is not None:
        last_sync_at = client.tokens.get("last_sync_at")
    return HealthProviderStatus(
        configured=configured,
        connected=connected,
        last_sync_at=last_sync_at,
    )


def _sources_response(ctx: AthleteContext) -> HealthSourcesResponse:
    provider = get_health_provider(db_path=ctx.db_path)
    garmin = _garmin_provider_status(ctx)
    google = _google_provider_status(ctx)
    return HealthSourcesResponse(
        provider=provider,  # type: ignore[arg-type]
        provider_effective=resolve_health_provider(
            provider,
            garmin_connected=garmin.connected,
            google_connected=google.connected,
        ),  # type: ignore[arg-type]
        garmin=garmin,
        google_health=google,
    )


@router.get("/sources", response_model=HealthSourcesResponse)
def get_sources(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> HealthSourcesResponse:
    """Providers de métriques automatiques : connexion, fraîcheur, préférence."""
    return _sources_response(ctx)


@router.put("/sources/provider", response_model=HealthSourcesResponse)
def put_sources_provider(
    payload: HealthProviderUpdate,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> HealthSourcesResponse:
    """Change le provider préféré (auto = Garmin prioritaire)."""
    set_health_provider(payload.provider, db_path=ctx.db_path)
    return _sources_response(ctx)


def _weight_response(ctx: AthleteContext) -> WeightResponse:
    entry = latest_weight_entry(db_path=ctx.db_path)
    weight = entry[1] if entry is not None else None
    return WeightResponse(
        weight_kg=weight,
        date=entry[0] if entry is not None else None,
        ftp_w=ctx.ftp,
        wkg=power_to_weight(ctx.ftp, weight),
    )


@router.get("/weight", response_model=WeightResponse)
def get_weight(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> WeightResponse:
    """Dernier poids connu + rapport poids/puissance dérivé."""
    return _weight_response(ctx)


@router.put("/weight", response_model=WeightResponse)
def put_weight(
    payload: WeightSubmit,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> WeightResponse:
    """Enregistre un poids (upsert ciblé) sans toucher aux autres métriques."""
    target_date = payload.date or dt.date.today().isoformat()
    set_weight(target_date, payload.weight_kg, db_path=ctx.db_path)
    return _weight_response(ctx)
