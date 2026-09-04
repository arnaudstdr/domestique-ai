"""Endpoints d'analyse de charge et de surentraînement."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from domestique_ai.api.deps import get_athlete_context
from domestique_ai.api.logging import get_logger
from domestique_ai.api.schemas import (
    Alert,
    FtpProjectionResponse,
    LoadCurrent,
    LoadPoint,
    LoadResponse,
    OvertrainingIndicators,
    OvertrainingResponse,
    RideVolumeResponse,
    SyncResult,
    TrendsResponse,
    VolumePeriod,
)
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.processing.analyzer import (
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
    recalculate_training_loads,
)
from domestique_ai.processing.morning_metrics import detect_morning_alerts
from domestique_ai.processing.overtraining import detect_overtraining_signals
from domestique_ai.processing.trends import get_ftp_projection, get_trends

router = APIRouter(prefix="/api/metrics", tags=["metrics"])
log = get_logger("metrics")


def _zone_from_tsb(tsb: float) -> tuple[str, str]:
    """Retourne (zone code anglais, libellé FR) — mêmes seuils que le dashboard."""
    if tsb > 5:
        return "freshness", "Frais"
    if tsb >= -10:
        return "optimal", "Optimal"
    if tsb >= -20:
        return "overreaching", "Fatigué"
    return "overtraining", "Surentraîné"


@router.get("/load", response_model=LoadResponse)
def get_load(
    days: int = 90,
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> LoadResponse:
    """Dernier point CTL/ATL/TSB + historique sur N jours (90 par défaut)."""
    activities = fetch_activities_from_db(ctx=ctx)
    curves = calculate_ctl_atl_tsb(activities, end_date=dt.date.today())
    if not curves:
        return LoadResponse(current=None, history=[])

    cutoff = dt.date.today() - dt.timedelta(days=max(days, 1))
    filtered = [c for c in curves if c["date"] >= cutoff.isoformat()]
    history = [
        LoadPoint(date=c["date"], ctl=c["CTL"], atl=c["ATL"], tsb=c["TSB"]) for c in filtered
    ]
    last = curves[-1]
    zone, label = _zone_from_tsb(last["TSB"])
    return LoadResponse(
        current=LoadCurrent(
            ctl=last["CTL"],
            atl=last["ATL"],
            tsb=last["TSB"],
            zone=zone,  # type: ignore[arg-type]
            zone_label_fr=label,
        ),
        history=history,
    )


_INDICATOR_LEVEL: dict[str, str] = {
    "tsb_chronic": "danger",
    "strain": "danger",
    "monotony": "warning",
    "weekly_jump": "warning",
}


@router.get("/overtraining", response_model=OvertrainingResponse)
def get_overtraining(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> OvertrainingResponse:
    """Indicateurs auto + dérive matinale, agrégés en un seul payload."""
    auto = detect_overtraining_signals(ctx=ctx)
    morning = detect_morning_alerts(db_path=ctx.db_path)

    alerts: list[Alert] = []
    for raw in auto.get("alerts") or []:
        indicator = raw.get("indicator", "unknown")
        alerts.append(
            Alert(
                type=indicator,
                level=_INDICATOR_LEVEL.get(indicator, "warning"),  # type: ignore[arg-type]
                message=raw.get("message", ""),
            )
        )
    for raw in morning:
        severity = raw.get("severity", "warning")
        level = "danger" if severity == "critical" else "warning"
        delta_pct = raw.get("delta_pct", 0.0)
        arrow = "↓" if delta_pct < 0 else "↑"
        message = (
            f"{raw.get('metric')} {arrow} {delta_pct:+.1f}% vs baseline "
            f"({raw.get('latest', 0):.1f} le {raw.get('latest_date')})"
        )
        alerts.append(
            Alert(
                type=f"morning_{raw.get('metric')}",
                level=level,  # type: ignore[arg-type]
                message=message,
            )
        )

    chronic = auto.get("tsb_chronic") or {}
    monstrain = auto.get("monotony_strain") or {}
    weekly = auto.get("weekly_jump") or {}

    indicators = OvertrainingIndicators(
        chronic_tsb=chronic.get("mean_tsb") if chronic.get("available") else None,
        monotony=monstrain.get("monotony") if monstrain.get("available") else None,
        strain=monstrain.get("strain") if monstrain.get("available") else None,
        weekly_jump_pct=weekly.get("delta_pct") if weekly.get("available") else None,
    )
    return OvertrainingResponse(alerts=alerts, indicators=indicators)


def _is_ride(sport_type: str | None) -> bool:
    """Filtre vélo : `sport_type` contient 'Ride' (Ride, VirtualRide, …)."""
    return bool(sport_type) and "Ride" in sport_type


@router.get("/ride-volume", response_model=RideVolumeResponse)
def get_ride_volume(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> RideVolumeResponse:
    """Volume cyclisme (distance + temps) sur l'année civile + la semaine ISO en cours.

    N'inclut que les activités dont `sport_type` contient "Ride" (Ride,
    VirtualRide, MountainBikeRide, GravelRide, EBikeRide…).
    """
    today = dt.date.today()
    year_start = dt.date(today.year, 1, 1)
    week_start = today - dt.timedelta(days=today.weekday())

    year = {"distance_m": 0.0, "duration_sec": 0}
    week = {"distance_m": 0.0, "duration_sec": 0}

    for act in fetch_activities_from_db(ctx=ctx):
        if not _is_ride(act.get("sport_type")):
            continue
        date_str = act.get("date")
        if not date_str:
            continue
        try:
            act_date = dt.datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        distance = float(act.get("distance") or 0)
        duration = int(act.get("duration") or 0)
        if act_date >= year_start:
            year["distance_m"] += distance
            year["duration_sec"] += duration
        if act_date >= week_start:
            week["distance_m"] += distance
            week["duration_sec"] += duration

    return RideVolumeResponse(
        year=VolumePeriod(
            distance_km=round(year["distance_m"] / 1000, 1),
            duration_sec=int(year["duration_sec"]),
        ),
        week=VolumePeriod(
            distance_km=round(week["distance_m"] / 1000, 1),
            duration_sec=int(week["duration_sec"]),
        ),
    )


@router.get("/trends", response_model=TrendsResponse)
def get_long_term_trends(
    period: Literal["3m", "6m", "1y", "all"] = Query(default="6m"),
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> TrendsResponse:
    """Tendances longue durée : CTL/ATL/TSB + volumes mensuels + zones par mois.

    La résolution de la courbe de charge est adaptée à la période demandée
    (jour pour 3 mois, semaine pour 6 mois et 1 an, mois pour ``all``) afin de
    garder un nombre de points raisonnable côté graphique.
    """
    raw = get_trends(period=period, ctx=ctx)
    return TrendsResponse(**raw)


@router.post("/recalculate", response_model=SyncResult)
def post_recalculate(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> SyncResult:
    """Recalcule la charge d'entraînement (hr-TSS / TSS puissance) de l'athlète.

    À relancer après une modification du profil HR ou FTP : les scores déjà
    persistés ne sont pas recalculés automatiquement.
    """
    log.info("Recalcul charge : démarrage…")
    try:
        updated = recalculate_training_loads(ctx=ctx)
    except Exception as exc:  # noqa: BLE001
        log.exception("Recalcul charge : exception")
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
    log.info("Recalcul charge : %d ligne(s) mises à jour.", updated)
    return SyncResult(status="done", updated=updated)


@router.get("/ftp-projection", response_model=FtpProjectionResponse)
def get_projection_ftp(
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> FtpProjectionResponse:
    """Projection FTP à 4 semaines, heuristique CTL + part Z4-Z5.

    Heuristique : +1 % de FTP par +5 points de CTL net sur 28 jours, plafonné
    à ±5 %. La confiance qualitative (``low/medium/high``) reflète la
    profondeur de l'historique et la présence d'un stimulus seuil/VO2max
    plausible (part Z4-Z5 entre 4 % et 25 %).
    """
    return FtpProjectionResponse(**get_ftp_projection(ctx=ctx))
