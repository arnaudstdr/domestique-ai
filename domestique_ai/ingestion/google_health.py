"""Client et ingestion pour la Google Health API (successeur de la Fitbit Web API).

Cette API REST cloud permet de lire les données de santé Fitbit, Pixel Watch et
autres appareils connectés au compte Google Health : HRV, FC repos, sommeil,
SpO2, fréquence respiratoire, température cutanée, pas, calories, etc.

Références :
- https://developers.google.com/health
- https://developers.google.com/health/migration
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from domestique_ai.api.logging import get_logger
from domestique_ai.config import (
    get_google_health_credentials,
    get_google_health_tokens_path,
)

log = get_logger("google_health")

_GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GOOGLE_HEALTH_API_BASE = "https://health.googleapis.com/v4"

# Scopes requis pour lire les métriques matinales et d'activité.
# Tous les scopes Google Health sont "Restricted" et nécessitent une review.
GOOGLE_HEALTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]

# dataTypes Google Health utilisés par l'intégration.
DATA_TYPE_DAILY_HRV = "daily-heart-rate-variability"
DATA_TYPE_DAILY_RHR = "daily-resting-heart-rate"
DATA_TYPE_SLEEP = "sleep"
DATA_TYPE_DAILY_SPO2 = "daily-oxygen-saturation"
DATA_TYPE_RESPIRATORY_SLEEP = "respiratory-rate-sleep-summary"
DATA_TYPE_SKIN_TEMP = "daily-sleep-temperature-derivations"
DATA_TYPE_STEPS = "steps"
DATA_TYPE_ACTIVE_CALORIES = "active-calories"


class GoogleHealthAuthError(Exception):
    """Token absent, invalide ou échec d'authentification."""


class GoogleHealthAPIError(Exception):
    """Erreur retournée par l'API Google Health."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoogleHealthClient:
    """Client OAuth2 + API Google Health.

    Gère la persistance des tokens, le refresh automatique et les appels REST
    de base. Inspiré du pattern de ``StravaClient``.
    """

    def __init__(
        self,
        tokens: dict[str, Any],
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        tokens_path: Path | None = None,
    ) -> None:
        self.tokens = tokens
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.tokens_path = tokens_path

    @classmethod
    def from_tokens_file(
        cls,
        tokens_path: Path | None = None,
    ) -> GoogleHealthClient | None:
        """Charge un client depuis le fichier de tokens JSON.

        Retourne ``None`` si le fichier n'existe pas ou si les credentials
        ne sont pas configurés.
        """
        path = Path(tokens_path) if tokens_path else get_google_health_tokens_path()
        client_id, client_secret, redirect_uri = get_google_health_credentials()
        if not client_id or not client_secret:
            log.debug("Google Health credentials non configurés.")
            return None
        if not path.exists():
            return None
        try:
            tokens = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Lecture des tokens Google Health échouée.")
            return None
        return cls(tokens, client_id, client_secret, redirect_uri, tokens_path=path)

    def is_authenticated(self) -> bool:
        """True si un access token valide (potentiellement rafraîchissable) est présent."""
        return bool(self.tokens.get("access_token") or self.tokens.get("refresh_token"))

    def save_tokens(self) -> None:
        """Persiste les tokens courants sur disque."""
        if not self.tokens_path:
            return
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
        self.tokens_path.write_text(
            json.dumps(self.tokens, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _refresh_access_token(self) -> None:
        """Rafraîchit l'access token via le refresh token."""
        refresh_token = self.tokens.get("refresh_token")
        if not refresh_token:
            raise GoogleHealthAuthError("Pas de refresh token disponible.")
        response = requests.post(
            _GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if response.status_code == 401:
            raise GoogleHealthAuthError(
                "Refresh token rejeté par Google (probablement révoqué ou expiré)."
            )
        response.raise_for_status()
        data = response.json()
        # Google ne renvoie pas toujours un nouveau refresh_token.
        if "refresh_token" not in data:
            data["refresh_token"] = refresh_token
        self.tokens.update(data)
        self.save_tokens()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> dict[str, Any]:
        """Appel authentifié à l'API Google Health avec refresh automatique."""
        access_token = self.tokens.get("access_token")
        if not access_token:
            raise GoogleHealthAuthError("Pas d'access token.")

        url = f"{_GOOGLE_HEALTH_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        if json_payload is not None:
            headers["Content-Type"] = "application/json"

        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_payload,
            timeout=30,
        )

        if response.status_code == 401 and retry_on_401:
            self._refresh_access_token()
            return self._request(method, path, params, json_payload, retry_on_401=False)

        if response.status_code >= 400:
            try:
                detail = response.json()
            except requests.JSONDecodeError:
                detail = response.text
            raise GoogleHealthAPIError(
                f"Google Health API {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        if response.status_code == 204:
            return {}
        return response.json()

    # -----------------------------------------------------------------------
    # OAuth helpers
    # -----------------------------------------------------------------------

    def get_auth_url(self, state: str | None = None) -> str:
        """Construit l'URL de consentement Google OAuth2."""
        state = state or secrets.token_urlsafe(16)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_HEALTH_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{_GOOGLE_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        """Échange un code d'autorisation contre des tokens."""
        response = requests.post(
            _GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except requests.JSONDecodeError:
                detail = response.text
            raise GoogleHealthAuthError(f"Échec échange code OAuth: {detail}")
        response.raise_for_status()
        self.tokens = response.json()
        self.save_tokens()
        return self.tokens

    def revoke_tokens(self) -> None:
        """Révoque le token auprès de Google et efface le fichier local."""
        access_token = self.tokens.get("access_token")
        if access_token:
            try:
                requests.post(
                    _GOOGLE_REVOKE_URL,
                    params={"token": access_token},
                    timeout=30,
                )
            except requests.RequestException:
                log.exception("Révocation token Google Health échouée (best-effort).")
        if self.tokens_path and self.tokens_path.exists():
            self.tokens_path.unlink()
        self.tokens = {}

    # -----------------------------------------------------------------------
    # API helpers
    # -----------------------------------------------------------------------

    def fetch_daily_rollups(
        self,
        data_type: str,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict[str, dict[str, Any]]:
        """Récupère les rollups journaliers d'un dataType sur une plage de dates.

        Retourne un dict ``{date_iso: data_point}`` où ``data_point`` est le
        point de données brut tel que renvoyé par l'API.
        """
        start = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.UTC)
        end = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        filter_str = f"start_time>{start.isoformat()},end_time<={end.isoformat()}"
        data = self._request(
            "GET",
            f"/users/me/dataTypes/{data_type}/dailyRollups",
            params={"filter": filter_str},
        )
        points = data.get("dataPoints", [])
        result: dict[str, dict[str, Any]] = {}
        for point in points:
            date_str = _extract_date_from_point(point)
            if date_str:
                result[date_str] = point
        return result

    def fetch_sleep_sessions(
        self,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict[str, list[dict[str, Any]]]:
        """Récupère les sessions de sommeil par date.

        Retourne un dict ``{date_iso: [sessions]}``. La date clé est la date de
        réveil (end time) de la session, convertie en civil time si possible.
        """
        start = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.UTC)
        end = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        filter_str = f"start_time>{start.isoformat()},end_time<={end.isoformat()}"
        data = self._request(
            "GET",
            f"/users/me/dataTypes/{DATA_TYPE_SLEEP}/dataPoints",
            params={"filter": filter_str},
        )
        points = data.get("dataPoints", [])
        result: dict[str, list[dict[str, Any]]] = {}
        for point in points:
            date_str = _extract_end_date_from_point(point)
            if date_str:
                result.setdefault(date_str, []).append(point)
        return result

    # -----------------------------------------------------------------------
    # Sync haut niveau
    # -----------------------------------------------------------------------

    def fetch_morning_data(
        self,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict[str, dict[str, Any]]:
        """Agrège toutes les données matinales disponibles par date.

        Retourne un dict ``{date_iso: {"hrv_ms": ..., "sleep_hours": ..., ...}}``.
        Les valeurs absentes sont ``None``.
        """
        hrv_by_date = self.fetch_daily_rollups(DATA_TYPE_DAILY_HRV, start_date, end_date)
        rhr_by_date = self.fetch_daily_rollups(DATA_TYPE_DAILY_RHR, start_date, end_date)
        spo2_by_date = self.fetch_daily_rollups(DATA_TYPE_DAILY_SPO2, start_date, end_date)
        respiratory_by_date = self.fetch_daily_rollups(
            DATA_TYPE_RESPIRATORY_SLEEP, start_date, end_date
        )
        skin_temp_by_date = self.fetch_daily_rollups(DATA_TYPE_SKIN_TEMP, start_date, end_date)
        steps_by_date = self.fetch_daily_rollups(DATA_TYPE_STEPS, start_date, end_date)
        calories_by_date = self.fetch_daily_rollups(DATA_TYPE_ACTIVE_CALORIES, start_date, end_date)
        sleep_sessions_by_date = self.fetch_sleep_sessions(start_date, end_date)

        result: dict[str, dict[str, Any]] = {}
        dates = _all_dates(start_date, end_date)
        for date_str in dates:
            entry: dict[str, Any] = {
                "hrv_ms": _extract_hrv(hrv_by_date.get(date_str)),
                "resting_hr": _extract_rhr(rhr_by_date.get(date_str)),
                "spo2_avg_pct": _extract_spo2(spo2_by_date.get(date_str)),
                "respiratory_rate_avg_bpm": _extract_respiratory(respiratory_by_date.get(date_str)),
                "skin_temp_delta_c": _extract_skin_temp(skin_temp_by_date.get(date_str)),
                "steps": _extract_steps(steps_by_date.get(date_str)),
                "active_calories": _extract_active_calories(calories_by_date.get(date_str)),
            }
            sleep_summary = _summarize_sleep_sessions(sleep_sessions_by_date.get(date_str, []))
            entry.update(sleep_summary)
            result[date_str] = entry
        return result


def _all_dates(start: dt.date, end: dt.date) -> list[str]:
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += dt.timedelta(days=1)
    return dates


def _extract_date_from_point(point: dict[str, Any] | None) -> str | None:
    if not point:
        return None
    # Les daily rollups ont généralement un champ "date" au format YYYY-MM-DD.
    if "date" in point:
        return point["date"]
    # Fallback sur startTime civil.
    start = point.get("startTime") or point.get("start_time")
    if start:
        return _civil_date_from_iso(start)
    return None


def _extract_end_date_from_point(point: dict[str, Any] | None) -> str | None:
    if not point:
        return None
    end = point.get("endTime") or point.get("end_time")
    if end:
        return _civil_date_from_iso(end)
    return None


def _civil_date_from_iso(iso: str) -> str | None:
    """Extrait la date YYYY-MM-DD d'un timestamp ISO, en ignorant l'heure/UTC."""
    try:
        # On prend les 10 premiers caractères si c'est un format ISO complet.
        if len(iso) >= 10:
            return iso[:10]
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Extracteurs par data type — défensifs, retournent None si la valeur manque.
# ---------------------------------------------------------------------------


def _extract_hrv(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    value = point.get("value", {})
    # DailyHeartRateVariability.averageHeartRateVariabilityMilliseconds
    ms = value.get("averageHeartRateVariabilityMilliseconds")
    if ms is None:
        ms = value.get("average_heart_rate_variability_milliseconds")
    if ms is not None:
        try:
            return float(ms)
        except (TypeError, ValueError):
            return None
    return None


def _extract_rhr(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    value = point.get("value", {})
    # DailyRestingHeartRate.beatsPerMinute
    bpm = value.get("beatsPerMinute") or value.get("beats_per_minute")
    if bpm is not None:
        try:
            return float(bpm)
        except (TypeError, ValueError):
            return None
    return None


def _extract_spo2(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    value = point.get("value", {})
    pct = value.get("averagePercentage") or value.get("average_percentage")
    if pct is None:
        pct = value.get("percentage")
    if pct is not None:
        try:
            return float(pct)
        except (TypeError, ValueError):
            return None
    return None


def _extract_respiratory(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    value = point.get("value", {})
    rate = value.get("breathsPerMinute") or value.get("breaths_per_minute")
    if rate is not None:
        try:
            return float(rate)
        except (TypeError, ValueError):
            return None
    return None


def _extract_skin_temp(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    value = point.get("value", {})
    delta = value.get("temperatureDeltaCelsius") or value.get("temperature_delta_celsius")
    if delta is None:
        delta = value.get("delta")
    if delta is not None:
        try:
            return float(delta)
        except (TypeError, ValueError):
            return None
    return None


def _extract_steps(point: dict[str, Any] | None) -> int | None:
    if not point:
        return None
    value = point.get("value", {})
    steps = value.get("count") or value.get("steps")
    if steps is not None:
        try:
            return int(steps)
        except (TypeError, ValueError):
            return None
    return None


def _extract_active_calories(point: dict[str, Any] | None) -> int | None:
    if not point:
        return None
    value = point.get("value", {})
    kcal = value.get("kcal") or value.get("calories") or value.get("energyKcal")
    if kcal is not None:
        try:
            return int(kcal)
        except (TypeError, ValueError):
            return None
    return None


def _summarize_sleep_sessions(sessions: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Agrège une ou plusieurs sessions de sommeil en métriques journalières."""
    result: dict[str, Any] = {
        "sleep_hours": None,
        "sleep_deep_min": None,
        "sleep_rem_min": None,
        "sleep_light_min": None,
        "sleep_awake_min": None,
    }
    if not sessions:
        return result

    total_sleep_sec = 0
    deep_sec = 0
    rem_sec = 0
    light_sec = 0
    awake_sec = 0

    for session in sessions or []:
        value = session.get("value", {})
        stages = value.get("stages") or value.get("sleepStages") or []
        for stage in stages:
            stage_type = (
                stage.get("stage") or stage.get("type") or stage.get("sleepStageType") or ""
            ).upper()
            seconds = stage.get("seconds") or stage.get("durationSeconds") or 0
            try:
                seconds = int(seconds)
            except (TypeError, ValueError):
                seconds = 0
            if stage_type in ("DEEP", "DEEP_SLEEP"):
                deep_sec += seconds
            elif stage_type in ("REM", "REM_SLEEP"):
                rem_sec += seconds
            elif stage_type in ("LIGHT", "LIGHT_SLEEP", "ASLEEP"):
                light_sec += seconds
            elif stage_type in ("AWAKE", "AWAKE_SLEEP", "WAKE"):
                awake_sec += seconds

        # Si les stades ne sont pas fournis, on utilise la durée totale de la session.
        start = session.get("startTime") or session.get("start_time")
        end = session.get("endTime") or session.get("end_time")
        if start and end:
            try:
                start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
                session_sec = int((end_dt - start_dt).total_seconds())
                if not stages:
                    light_sec += max(0, session_sec - awake_sec)
                total_sleep_sec += max(0, session_sec - awake_sec)
            except (ValueError, TypeError):
                pass
        else:
            total_sleep_sec += deep_sec + rem_sec + light_sec

    total_min = total_sleep_sec / 60
    if total_min > 0:
        result["sleep_hours"] = round(total_min / 60, 2)
    if deep_sec > 0:
        result["sleep_deep_min"] = deep_sec // 60
    if rem_sec > 0:
        result["sleep_rem_min"] = rem_sec // 60
    if light_sec > 0:
        result["sleep_light_min"] = light_sec // 60
    if awake_sec > 0:
        result["sleep_awake_min"] = awake_sec // 60

    return result


def sync_google_health_morning_metrics(
    client: GoogleHealthClient,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Récupère les données Google Health et les écrit dans ``morning_metrics``.

    Par défaut, synchronise les 7 derniers jours (idéal pour un cron quotidien).
    Respecte une éventuelle saisie manuelle : si ``sleep_score_computed=0``
    (saisie manuelle), le ``sleep_score`` calculé n'écrase pas la valeur
    existante.

    Retourne un résumé : ``{"synced_dates": [...], "skipped_dates": [...]}``.
    """
    from domestique_ai.processing.morning_metrics import (
        calculate_readiness_score,
        calculate_sleep_score,
        fetch_morning_entry,
        save_morning_entry,
    )

    if end_date is None:
        end_date = dt.date.today()
    if start_date is None:
        start_date = end_date - dt.timedelta(days=7)

    data_by_date = client.fetch_morning_data(start_date, end_date)

    synced: list[str] = []
    skipped: list[str] = []

    for date_str, data in data_by_date.items():
        existing = fetch_morning_entry(date_str, db_path=db_path)
        manual_sleep_score = (
            existing is not None
            and existing.get("sleep_score") is not None
            and existing.get("sleep_score_computed") == 0
        )

        if manual_sleep_score:
            sleep_score = existing.get("sleep_score")
            sleep_score_computed = 0
        else:
            sleep_score = calculate_sleep_score(
                data.get("sleep_hours"),
                data.get("sleep_deep_min"),
                data.get("sleep_rem_min"),
                data.get("sleep_light_min"),
                data.get("sleep_awake_min"),
            )
            sleep_score_computed = 1 if sleep_score is not None else None

        readiness_score = calculate_readiness_score(
            data.get("hrv_ms"),
            data.get("resting_hr"),
            data.get("sleep_hours"),
            db_path=db_path,
        )

        kwargs: dict[str, Any] = {
            "date": date_str,
            "hrv_ms": data.get("hrv_ms"),
            "resting_hr": data.get("resting_hr"),
            "sleep_hours": data.get("sleep_hours"),
            "sleep_score": sleep_score,
            "sleep_score_computed": sleep_score_computed,
            "spo2_avg_pct": data.get("spo2_avg_pct"),
            "respiratory_rate_avg_bpm": data.get("respiratory_rate_avg_bpm"),
            "skin_temp_delta_c": data.get("skin_temp_delta_c"),
            "sleep_deep_min": data.get("sleep_deep_min"),
            "sleep_rem_min": data.get("sleep_rem_min"),
            "sleep_light_min": data.get("sleep_light_min"),
            "sleep_awake_min": data.get("sleep_awake_min"),
            "steps": data.get("steps"),
            "active_calories": data.get("active_calories"),
            "readiness_score": readiness_score,
        }

        # On ne stocke que les dates ayant au moins une métrique automatique.
        metric_values = {k: v for k, v in kwargs.items() if k != "date"}
        if all(v is None for v in metric_values.values()):
            skipped.append(date_str)
            continue

        # Conserve les champs manuels existants (stress, notes) si présents.
        if existing:
            for manual_field in ("stress_score", "notes"):
                if (
                    existing.get(manual_field) is not None
                    and metric_values.get(manual_field) is None
                ):
                    kwargs[manual_field] = existing[manual_field]

        save_morning_entry(db_path=db_path, **kwargs)
        synced.append(date_str)

    log.info(
        "Google Health sync terminée : %d dates syncées, %d dates sans donnée.",
        len(synced),
        len(skipped),
    )
    return {"synced_dates": synced, "skipped_dates": skipped}
