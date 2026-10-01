"""Ingestion des métriques de récupération Garmin Connect (alternative Google Health).

Même sink que Google Health : la table ``morning_metrics``, alimentée via
``build_provider_morning_payload`` + ``save_morning_entry`` (scores locaux
identiques, préservation des saisies manuelles). La préférence de provider
(``health_provider`` dans ``sync_meta``) arbitre la cohabitation :

- ``auto`` (défaut) : Garmin est prioritaire, Google Health ne remplit que les
  jours sans données Garmin (``source`` absente ou ``google_health``).
- ``garmin`` : Google Health ne sync plus les métriques automatiques.
- ``google_health`` : la sync santé Garmin est court-circuitée.

⚠️ API non officielle (``garminconnect``), endpoints susceptibles de changer
sans préavis. Fenêtre volontairement courte (7 j par défaut, 30 j max).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.ingestion.db import set_sync_meta

log = get_logger("garmin_health")

# Fenêtre de sync (API non officielle : rester sobre).
DEFAULT_SYNC_DAYS = 7
MAX_SYNC_DAYS = 30

# Clés ``sync_meta`` (liées au statut de la dernière sync santé).
GARMIN_HEALTH_LAST_SYNC_KEY = "garmin_health_last_sync_at"
GARMIN_HEALTH_LAST_ERROR_KEY = "garmin_health_last_error"

# Mapping des niveaux de sommeil bruts Garmin (``sleepLevels[].activityLevel``)
# vers les types consommés par le front (hypnogramme).
_SLEEP_LEVEL_TYPES = {0: "DEEP", 1: "LIGHT", 2: "REM", 3: "AWAKE"}


class GarminHealthError(Exception):
    """Erreur de récupération des métriques de santé Garmin."""


# ---------------------------------------------------------------------------
# Helpers de conversion défensifs
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return int(round(number)) if number is not None else None


def _normalize_gmt(value: Any) -> str | None:
    """Normalise un timestamp Garmin ``...T23:24:00.0`` (sans fuseau) en ISO UTC."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    if value.endswith("Z"):
        return value
    tail = value[10:]
    if "+" in tail or "-" in tail:
        return value
    return f"{value}Z"


def _all_dates(start: dt.date, end: dt.date) -> list[str]:
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += dt.timedelta(days=1)
    return dates


def _safe_call(func: Any, *args: Any) -> Any:
    """Appelle une méthode du client Garmin en avalant les erreurs (best-effort)."""
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 — une métrique absente ne doit pas casser la sync
        log.warning(
            "Garmin Health : appel %s échoué (best-effort).", getattr(func, "__name__", func)
        )
        return None


# ---------------------------------------------------------------------------
# Extracteurs par métrique — purs, défensifs, testables sans réseau.
# ---------------------------------------------------------------------------


def extract_hrv(payload: Any) -> float | None:
    """HRV de la nuit (``hrvSummary.lastNightAvg``, repli moyenne hebdo)."""
    if not isinstance(payload, dict):
        return None
    summary = payload.get("hrvSummary") or payload.get("hrv_summary")
    if not isinstance(summary, dict):
        return None
    last_night = _as_float(summary.get("lastNightAvg"))
    if last_night is not None:
        return last_night
    return _as_float(summary.get("weeklyAvg"))


def extract_resting_hr(stats: Any) -> float | None:
    """FC de repos du jour (résumé d'activité Garmin)."""
    if not isinstance(stats, dict):
        return None
    return _as_float(stats.get("restingHeartRate"))


def extract_steps(stats: Any) -> int | None:
    if not isinstance(stats, dict):
        return None
    return _as_int(stats.get("totalSteps"))


def extract_active_calories(stats: Any) -> int | None:
    if not isinstance(stats, dict):
        return None
    calories = _as_float(stats.get("activeKilocalories"))
    return int(round(calories)) if calories is not None else None


def extract_body_battery(stats: Any) -> tuple[int | None, int | None]:
    """Body battery min/max du jour (valeurs natives Garmin, bonus)."""
    if not isinstance(stats, dict):
        return (None, None)
    return (
        _as_int(stats.get("bodyBatteryLowestValue")),
        _as_int(stats.get("bodyBatteryHighestValue")),
    )


def extract_spo2(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("averageSpO2", "lastNightAvg", "lastSevenDaysAvgSpO2"):
        value = _as_float(payload.get(key))
        if value is not None:
            return value
    return None


def extract_respiration(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("avgSleepRespirationValue", "avgWakingRespirationValue"):
        value = _as_float(payload.get(key))
        if value is not None:
            return value
    return None


def extract_training_readiness(payload: Any) -> int | None:
    """Score de training readiness natif (bonus) — dict ou liste de snapshots."""
    if isinstance(payload, dict):
        return _as_int(payload.get("score"))
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and entry.get("inputContext") == "AFTER_WAKEUP_RESET":
                return _as_int(entry.get("score"))
        for entry in payload:
            if isinstance(entry, dict):
                return _as_int(entry.get("score"))
    return None


def _extract_sleep_stages(payload: dict[str, Any]) -> list[dict[str, str]]:
    levels = payload.get("sleepLevels")
    if not isinstance(levels, list):
        dto = payload.get("dailySleepDTO")
        levels = dto.get("sleepLevels") if isinstance(dto, dict) else None
    if not isinstance(levels, list):
        return []
    stages: list[dict[str, str]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        start = _normalize_gmt(level.get("startGMT"))
        end = _normalize_gmt(level.get("endGMT"))
        level_value = _as_int(level.get("activityLevel"))
        stage_type = _SLEEP_LEVEL_TYPES.get(level_value) if level_value is not None else None
        if start and end and stage_type:
            stages.append({"start": start, "end": end, "type": stage_type})
    stages.sort(key=lambda stage: stage["start"])
    return stages


def extract_sleep(payload: Any) -> dict[str, Any]:
    """Sommeil d'une nuit Garmin → colonnes ``morning_metrics``.

    Clé = date de réveil (comportement Garmin identique à Google Health).
    Retourne un dict vide si aucune donnée exploitable.
    """
    result: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return result
    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        dto = {}

    deep = _as_int(dto.get("deepSleepSeconds"))
    rem = _as_int(dto.get("remSleepSeconds"))
    light = _as_int(dto.get("lightSleepSeconds"))
    awake = _as_int(dto.get("awakeSleepSeconds"))
    sleep_sec = _as_float(dto.get("sleepTimeSeconds"))
    if sleep_sec is None:
        parts = [value for value in (deep, rem, light) if value is not None]
        sleep_sec = float(sum(parts)) if parts else None

    if sleep_sec is not None and sleep_sec > 0:
        result["sleep_hours"] = round(sleep_sec / 3600.0, 2)
    if deep is not None:
        result["sleep_deep_min"] = deep // 60
    if rem is not None:
        result["sleep_rem_min"] = rem // 60
    if light is not None:
        result["sleep_light_min"] = light // 60
    if awake is not None:
        result["sleep_awake_min"] = awake // 60

    scores = dto.get("sleepScores")
    if isinstance(scores, dict):
        overall = scores.get("overall")
        if isinstance(overall, dict):
            result["garmin_sleep_score"] = _as_int(overall.get("value"))
        else:
            result["garmin_sleep_score"] = _as_int(overall)

    stages = _extract_sleep_stages(payload)
    if stages:
        result["sleep_stages"] = stages
    return result


def extract_sleep_fallbacks(payload: Any) -> dict[str, float]:
    """Valeurs HRV/SpO2/respiration embarquées dans le sommeil Garmin.

    Utilisées uniquement en repli quand les endpoints dédiés ne répondent pas.
    """
    if not isinstance(payload, dict):
        return {}
    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        return {}
    fallbacks: dict[str, float] = {}
    for key, field in (
        ("avgSleepHRV", "hrv_ms"),
        ("avgSpO2", "spo2_avg_pct"),
        ("avgRespirationValue", "respiratory_rate_avg_bpm"),
    ):
        value = _as_float(dto.get(key))
        if value is not None:
            fallbacks[field] = value
    return fallbacks


def _weight_kg(value: Any) -> float | None:
    grams = _as_float(value)
    if grams is None:
        return None
    kg = grams / 1000.0
    if 20.0 <= kg <= 300.0:
        return round(kg, 2)
    return None


def extract_weight(payload: Any) -> float | None:
    """Dernier poids de la fenêtre (le plus récent), en kg."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("dateWeightList")
    if not isinstance(rows, list):
        rows = []
        summaries = payload.get("dailyWeightSummaries")
        if isinstance(summaries, list):
            for summary in summaries:
                if isinstance(summary, dict):
                    rows.extend(summary.get("allWeightMetrics") or [])
    rows = [row for row in rows if isinstance(row, dict)]
    if rows:
        rows.sort(key=lambda row: str(row.get("calendarDate") or ""))
        for row in reversed(rows):
            kg = _weight_kg(row.get("weight"))
            if kg is not None:
                return kg
    total_average = payload.get("totalAverage")
    if isinstance(total_average, dict):
        return _weight_kg(total_average.get("weight"))
    return None


def extract_weight_by_date(payload: Any) -> dict[str, float]:
    """Poids indexés par date civile (pour l'affectation au bon jour)."""
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("dateWeightList")
    if not isinstance(rows, list):
        rows = []
        summaries = payload.get("dailyWeightSummaries")
        if isinstance(summaries, list):
            for summary in summaries:
                if isinstance(summary, dict):
                    rows.extend(summary.get("allWeightMetrics") or [])
    weights: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_str = row.get("calendarDate")
        kg = _weight_kg(row.get("weight"))
        if isinstance(date_str, str) and kg is not None:
            weights[date_str[:10]] = kg
    return weights


# ---------------------------------------------------------------------------
# Fetch + sync haut niveau
# ---------------------------------------------------------------------------


def fetch_garmin_morning_data(
    client: Any,
    start_date: dt.date,
    end_date: dt.date,
) -> dict[str, dict[str, Any]]:
    """Agrège les métriques matinales Garmin par date (clé = date de réveil).

    Une métrique absente ou en échec vaut ``None`` — la sync aval décide de
    conserver ou non la ligne existante. Lève ``GarminHealthError`` si aucune
    donnée n'a pu être lue sur toute la fenêtre (auth cassée, API changée…).
    """
    result: dict[str, dict[str, Any]] = {
        date_str: {} for date_str in _all_dates(start_date, end_date)
    }

    body_payload = _safe_call(
        client.get_body_composition, start_date.isoformat(), end_date.isoformat()
    )
    weight_by_date = extract_weight_by_date(body_payload)

    payloads_seen = 1 if body_payload else 0
    for date_str in result:
        stats = _safe_call(client.get_stats, date_str)
        sleep = _safe_call(client.get_sleep_data, date_str)
        hrv = _safe_call(client.get_hrv_data, date_str)
        spo2 = _safe_call(client.get_spo2_data, date_str)
        respiration = _safe_call(client.get_respiration_data, date_str)
        readiness = _safe_call(client.get_morning_training_readiness, date_str)
        payloads_seen += sum(
            1 for payload in (stats, sleep, hrv, spo2, respiration, readiness) if payload
        )

        battery_min, battery_max = extract_body_battery(stats)
        entry: dict[str, Any] = {
            "hrv_ms": extract_hrv(hrv),
            "resting_hr": extract_resting_hr(stats),
            "steps": extract_steps(stats),
            "active_calories": extract_active_calories(stats),
            "spo2_avg_pct": extract_spo2(spo2),
            "respiratory_rate_avg_bpm": extract_respiration(respiration),
            "garmin_readiness_score": extract_training_readiness(readiness),
            "garmin_body_battery_min": battery_min,
            "garmin_body_battery_max": battery_max,
        }
        entry.update(extract_sleep(sleep))

        fallbacks = extract_sleep_fallbacks(sleep)
        for field, value in fallbacks.items():
            if entry.get(field) is None:
                entry[field] = value

        if date_str in weight_by_date:
            entry["weight_kg"] = weight_by_date[date_str]
        result[date_str] = entry

    if payloads_seen == 0:
        raise GarminHealthError(
            "Aucune donnée de santé Garmin récupérée sur la fenêtre — "
            "vérifie la connexion Garmin (ou réessaie plus tard)."
        )
    return result


def sync_garmin_health_morning_metrics(
    client: Any | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    *,
    ctx: AthleteContext | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Sync santé Garmin → ``morning_metrics`` (même pipeline que Google Health).

    Retourne ``{"synced_dates", "skipped_dates", "disabled"}``. ``disabled``
    vaut True quand la préférence athlète est ``google_health`` (Garmin ne doit
    pas écrire les métriques automatiques). Enregistre le statut
    (``last_sync_at`` / ``last_error``) dans ``sync_meta``.
    """
    from domestique_ai.ingestion.garmin import _assert_ctx_can_sync, _ingest_client_for
    from domestique_ai.processing.morning_metrics import (
        HEALTH_PROVIDER_GARMIN,
        HEALTH_PROVIDER_GOOGLE,
        build_provider_morning_payload,
        fetch_morning_entry,
        get_health_provider,
        has_auto_metrics,
        save_morning_entry,
        set_weight,
    )

    if end_date is None:
        end_date = dt.date.today()
    if start_date is None:
        start_date = end_date - dt.timedelta(days=DEFAULT_SYNC_DAYS)
    # Garde-fou API non officielle : jamais plus de MAX_SYNC_DAYS d'un coup.
    if (end_date - start_date).days > MAX_SYNC_DAYS:
        start_date = end_date - dt.timedelta(days=MAX_SYNC_DAYS)

    provider = get_health_provider(db_path=db_path)
    if provider == HEALTH_PROVIDER_GOOGLE:
        log.info("Garmin Health : sync ignorée (provider préféré = Google Health).")
        return {"synced_dates": [], "skipped_dates": [], "disabled": True}

    try:
        _assert_ctx_can_sync(ctx)
        if client is None:
            client = _ingest_client_for(ctx)
        data_by_date = fetch_garmin_morning_data(client, start_date, end_date)

        synced: list[str] = []
        skipped: list[str] = []
        for date_str, data in data_by_date.items():
            existing = fetch_morning_entry(date_str, db_path=db_path)

            # Pesage seul (pas de métrique de récupération) : upsert ciblé du
            # poids — ne pas marquer la journée « garmin » et écraser la
            # provenance d'un autre provider (Google remplit toujours le jour).
            if not has_auto_metrics(data):
                if data.get("weight_kg") is not None:
                    set_weight(date_str, data["weight_kg"], db_path=db_path)
                    synced.append(date_str)
                else:
                    skipped.append(date_str)
                continue

            kwargs = build_provider_morning_payload(existing, data, db_path=db_path)
            if kwargs is None:
                skipped.append(date_str)
                continue
            kwargs["source"] = HEALTH_PROVIDER_GARMIN
            for field in (
                "garmin_sleep_score",
                "garmin_readiness_score",
                "garmin_body_battery_min",
                "garmin_body_battery_max",
            ):
                if data.get(field) is not None:
                    kwargs[field] = data[field]
            save_morning_entry(date_str, db_path=db_path, **kwargs)
            synced.append(date_str)
    except Exception as exc:
        set_sync_meta(GARMIN_HEALTH_LAST_ERROR_KEY, str(exc)[:500], db_path=db_path)
        raise

    set_sync_meta(
        GARMIN_HEALTH_LAST_SYNC_KEY,
        dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        db_path=db_path,
    )
    set_sync_meta(GARMIN_HEALTH_LAST_ERROR_KEY, "", db_path=db_path)
    log.info(
        "Garmin Health sync terminée : %d dates syncées, %d dates sans donnée.",
        len(synced),
        len(skipped),
    )
    return {"synced_dates": synced, "skipped_dates": skipped, "disabled": False}
