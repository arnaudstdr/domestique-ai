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
DATA_TYPE_ACTIVE_ENERGY_BURNED = "active-energy-burned"


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
    de base. Même pattern que le client d'ingestion d'activités.
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

    def get_identity(self) -> dict[str, Any]:
        """Identité Health de l'utilisateur (inclut ``legacyUserId`` si Fitbit lié)."""
        return self._request("GET", "/users/me/identity")

    def fetch_data_points(
        self,
        data_type: str,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict[str, dict[str, Any]]:
        """Récupère les points de données bruts (méthode ``list``).

        Utilisé pour les data types Daily et les sessions. Retourne un dict
        ``{date_iso: data_point}`` où ``date_iso`` est extrait du point.
        """
        filter_str = _build_list_filter(data_type, start_date, end_date)
        data = self._request(
            "GET",
            f"/users/me/dataTypes/{data_type}/dataPoints",
            params={"filter": filter_str},
        )
        points = data.get("dataPoints", [])
        log.info(
            "Google Health %s : filtre=%r, %d point(s) reçu(s)",
            data_type,
            filter_str,
            len(points),
        )
        if points:
            log.info(
                "Google Health %s premier point brut : %s", data_type, json.dumps(points[0])[:600]
            )
        result: dict[str, dict[str, Any]] = {}
        for point in points:
            date_str = _extract_date_from_point(point)
            if date_str:
                result[date_str] = point
            else:
                log.warning(
                    "Google Health %s : date non extractible du point %s",
                    data_type,
                    json.dumps(point)[:300],
                )
        return result

    def fetch_daily_rollup(
        self,
        data_type: str,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict[str, dict[str, Any]]:
        """Récupère les rollups journaliers (méthode ``dailyRollUp``).

        Utilisé pour les data types Interval (steps, active-energy-burned).
        Retourne un dict ``{date_iso: rollup_point}``.
        """
        payload = {
            "range": {
                "start": _civil_date_time(start_date, 0, 0, 0),
                "end": _civil_date_time(end_date + dt.timedelta(days=1), 0, 0, 0),
            },
            "windowSizeDays": 1,
        }
        data = self._request(
            "POST",
            f"/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp",
            json_payload=payload,
        )
        points = data.get("rollupDataPoints", [])
        log.info(
            "Google Health %s rollup : %d point(s) reçu(s)",
            data_type,
            len(points),
        )
        if points:
            log.info(
                "Google Health %s premier rollup brut : %s",
                data_type,
                json.dumps(points[0])[:600],
            )
        result: dict[str, dict[str, Any]] = {}
        for point in points:
            date_str = _extract_civil_start_date_from_point(point)
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
        filter_str = _build_list_filter(DATA_TYPE_SLEEP, start_date, end_date)
        data = self._request(
            "GET",
            f"/users/me/dataTypes/{DATA_TYPE_SLEEP}/dataPoints",
            params={"filter": filter_str},
        )
        points = data.get("dataPoints", [])
        log.info(
            "Google Health sleep : filtre=%r, %d session(s) reçue(s)",
            filter_str,
            len(points),
        )
        if points:
            log.info(
                "Google Health sleep première session brute : %s",
                json.dumps(points[0])[:600],
            )
        result: dict[str, list[dict[str, Any]]] = {}
        for point in points:
            date_str = _extract_end_date_from_point(point)
            if date_str:
                result.setdefault(date_str, []).append(point)
            else:
                log.warning(
                    "Google Health sleep : date de fin non extractible du point %s",
                    json.dumps(point)[:300],
                )
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
        hrv_by_date = self.fetch_data_points(DATA_TYPE_DAILY_HRV, start_date, end_date)
        rhr_by_date = self.fetch_data_points(DATA_TYPE_DAILY_RHR, start_date, end_date)
        spo2_by_date = self.fetch_data_points(DATA_TYPE_DAILY_SPO2, start_date, end_date)
        respiratory_by_date = self.fetch_data_points(
            DATA_TYPE_RESPIRATORY_SLEEP, start_date, end_date
        )
        skin_temp_by_date = self.fetch_data_points(DATA_TYPE_SKIN_TEMP, start_date, end_date)
        steps_by_date = self.fetch_daily_rollup(DATA_TYPE_STEPS, start_date, end_date)
        calories_by_date = self.fetch_daily_rollup(
            DATA_TYPE_ACTIVE_ENERGY_BURNED, start_date, end_date
        )
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


def _point_payload(point: dict[str, Any] | None) -> dict[str, Any]:
    """Retourne le sous-dict propre au data type d'un point.

    L'API imbrique les valeurs sous une clé nommée d'après le data type
    (ex. ``dailyHeartRateVariability``, ``sleep``), à côté de ``dataSource``
    et ``name``. Retourne ce dict, ou ``{}`` s'il est introuvable.
    """
    if not point:
        return {}
    for key, val in point.items():
        if key in ("dataSource", "name"):
            continue
        if isinstance(val, dict):
            return val
    return {}


def _civil_date_obj_to_iso(obj: Any) -> str | None:
    """Convertit un objet date civil ``{year, month, day}`` en date ISO."""
    if not isinstance(obj, dict):
        return None
    try:
        return dt.date(obj["year"], obj["month"], obj["day"]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _extract_date_from_point(point: dict[str, Any] | None) -> str | None:
    if not point:
        return None
    payload = _point_payload(point)
    # Data types Daily : payload["date"] (string ou objet civil).
    date_val = payload.get("date")
    if isinstance(date_val, str):
        return date_val[:10]
    iso = _civil_date_obj_to_iso(date_val)
    if iso:
        return iso
    # Types Sample : payload["sampleTime"]["civilTime"]["date"].
    sample_time = payload.get("sampleTime")
    if isinstance(sample_time, dict):
        civil = sample_time.get("civilTime")
        iso = _civil_date_obj_to_iso(civil.get("date") if isinstance(civil, dict) else None)
        if iso:
            return iso
    # Racine (fallback défensif).
    date_val = point.get("date")
    if isinstance(date_val, str):
        return date_val[:10]
    iso = _civil_date_obj_to_iso(date_val)
    if iso:
        return iso
    start = point.get("startTime") or point.get("start_time")
    if isinstance(start, str):
        return _civil_date_from_iso(start)
    return None


def _extract_end_date_from_point(point: dict[str, Any] | None) -> str | None:
    if not point:
        return None
    payload = _point_payload(point)
    interval = payload.get("interval") or {}
    if isinstance(interval, dict):
        civil_end = interval.get("civilEndTime")
        iso = _civil_date_obj_to_iso(civil_end.get("date") if isinstance(civil_end, dict) else None)
        if iso:
            return iso
        end = interval.get("endTime")
        if isinstance(end, str):
            return _civil_date_from_iso(end)
    end = point.get("endTime") or point.get("end_time")
    if isinstance(end, str):
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


def _to_google_health_timestamp(ts: dt.datetime) -> str:
    """Formate un datetime UTC au format attendu par les filtres Google Health.

    Google Health rejette le format ``+00:00`` (erreur de syntaxe sur le ``+``).
    On utilise le suffixe ``Z`` standard.
    """
    utc = ts.astimezone(dt.UTC).replace(tzinfo=None)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _civil_date_str(date: dt.date) -> str:
    """Formate une date au format civil ISO 8601 attendu par les filtres."""
    return date.isoformat()


def _build_list_filter(data_type: str, start_date: dt.date, end_date: dt.date) -> str:
    """Construit le filtre AIP-160 correct pour un data type Google Health.

    Les data types Daily filtrent sur ``<data_type>.date``. Sleep et les
    sessions filtrent sur leur champ d'interval civil. Les types Sample
    (point-in-time) filtrent sur ``sample_time.civil_time``.
    """
    start = _civil_date_str(start_date)
    end = _civil_date_str(end_date + dt.timedelta(days=1))

    if data_type == DATA_TYPE_SLEEP:
        return (
            f'{DATA_TYPE_SLEEP}.interval.civil_end_time >= "{start}"'
            f' AND {DATA_TYPE_SLEEP}.interval.civil_end_time < "{end}"'
        )

    if data_type in {
        DATA_TYPE_DAILY_HRV,
        DATA_TYPE_DAILY_RHR,
        DATA_TYPE_DAILY_SPO2,
        DATA_TYPE_SKIN_TEMP,
    }:
        prefix = data_type.replace("-", "_")
        return f'{prefix}.date >= "{start}" AND {prefix}.date < "{end}"'

    # Types Sample (point-in-time), ex. respiratory-rate-sleep-summary.
    prefix = data_type.replace("-", "_")
    return (
        f'{prefix}.sample_time.civil_time >= "{start}"'
        f' AND {prefix}.sample_time.civil_time < "{end}"'
    )


def _civil_date_time(date: dt.date, hour: int, minute: int, second: int) -> dict[str, Any]:
    """Construit un objet CivilDateTime pour les requêtes dailyRollUp."""
    return {
        "date": {
            "year": date.year,
            "month": date.month,
            "day": date.day,
        },
        "time": {
            "hours": hour,
            "minutes": minute,
            "seconds": second,
            "nanos": 0,
        },
    }


def _extract_civil_start_date_from_point(point: dict[str, Any] | None) -> str | None:
    """Extrait la date de civilStartTime d'un rollup point."""
    if not point:
        return None
    civil = point.get("civilStartTime") or point.get("civil_start_time")
    if civil:
        date_obj = civil.get("date") or civil
        try:
            return dt.date(date_obj["year"], date_obj["month"], date_obj["day"]).isoformat()
        except (KeyError, TypeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Extracteurs par data type — défensifs, retournent None si la valeur manque.
# ---------------------------------------------------------------------------


def _extract_hrv(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    payload = _point_payload(point)
    # DailyHeartRateVariability.averageHeartRateVariabilityMilliseconds
    ms = payload.get("averageHeartRateVariabilityMilliseconds")
    if ms is None:
        value = point.get("value", {})
        ms = value.get("averageHeartRateVariabilityMilliseconds")
    if ms is not None:
        try:
            return float(ms)
        except (TypeError, ValueError):
            return None
    return None


def _extract_rhr(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    payload = _point_payload(point)
    # DailyRestingHeartRate.beatsPerMinute (parfois string côté API).
    bpm = payload.get("beatsPerMinute")
    if bpm is None:
        value = point.get("value", {})
        bpm = value.get("beatsPerMinute")
    if bpm is not None:
        try:
            return float(bpm)
        except (TypeError, ValueError):
            return None
    return None


def _extract_spo2(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    payload = _point_payload(point)
    # DailyOxygenSaturation.averagePercentage
    pct = payload.get("averagePercentage")
    if pct is None:
        value = point.get("value", {})
        pct = value.get("averagePercentage") or value.get("percentage")
    if pct is not None:
        try:
            return float(pct)
        except (TypeError, ValueError):
            return None
    return None


def _extract_respiratory(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    payload = _point_payload(point)
    # RespiratoryRateSleepSummary : breathsPerMinute global si présent.
    rate = payload.get("breathsPerMinute")
    if rate is not None:
        try:
            return float(rate)
        except (TypeError, ValueError):
            return None
    # Sinon moyenne des breathsPerMinute des sous-stats (deep/light/rem...).
    rates: list[float] = []
    for val in payload.values():
        if isinstance(val, dict) and val.get("breathsPerMinute") is not None:
            try:
                rates.append(float(val["breathsPerMinute"]))
            except (TypeError, ValueError):
                continue
    if rates:
        return round(sum(rates) / len(rates), 1)
    return None


def _extract_skin_temp(point: dict[str, Any] | None) -> float | None:
    if not point:
        return None
    payload = _point_payload(point)
    delta = payload.get("temperatureDeltaCelsius") or payload.get("temperature_delta_celsius")
    if delta is not None:
        try:
            return float(delta)
        except (TypeError, ValueError):
            return None
    # DailySleepTemperatureDerivations : delta = nightly - baseline.
    nightly = payload.get("nightlyTemperatureCelsius")
    baseline = payload.get("baselineTemperatureCelsius")
    if nightly is not None and baseline is not None:
        try:
            return round(float(nightly) - float(baseline), 2)
        except (TypeError, ValueError):
            return None
    return None


def _extract_steps(point: dict[str, Any] | None) -> int | None:
    if not point:
        return None
    # Format dailyRollUp réel : point.steps.countSum (string).
    steps_value = point.get("steps")
    if isinstance(steps_value, dict):
        count = steps_value.get("countSum") or steps_value.get("count_sum")
        if count is not None:
            try:
                return int(count)
            except (TypeError, ValueError):
                return None
    value = point.get("value", {}) or {}
    steps_value = value.get("steps")
    if isinstance(steps_value, dict):
        count = steps_value.get("countSum") or steps_value.get("count_sum")
        if count is not None:
            try:
                return int(count)
            except (TypeError, ValueError):
                return None
    # Format list brut : value.count / value.steps
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
    # Format dailyRollUp réel : point.activeEnergyBurned.kcalSum.
    energy_value = point.get("activeEnergyBurned")
    if isinstance(energy_value, dict):
        kcal = energy_value.get("kcalSum") or energy_value.get("energyKcalSum")
        if kcal is not None:
            try:
                return int(float(kcal))
            except (TypeError, ValueError):
                return None
    value = point.get("value", {}) or {}
    energy_value = value.get("activeEnergyBurned")
    if isinstance(energy_value, dict):
        kcal = energy_value.get("kcalSum") or energy_value.get("energyKcalSum")
        if kcal is not None:
            try:
                return int(float(kcal))
            except (TypeError, ValueError):
                return None
    # Format list brut
    kcal = value.get("kcal") or value.get("calories") or value.get("energyKcal")
    if kcal is not None:
        try:
            return int(kcal)
        except (TypeError, ValueError):
            return None
    return None


def _stage_seconds(stage: dict[str, Any]) -> int:
    """Durée d'un stade de sommeil en secondes.

    L'API réelle fournit ``startTime``/``endTime`` par stade ; l'ancien format
    mocké fournissait ``seconds``. Les deux sont supportés.
    """
    seconds = stage.get("seconds") or stage.get("durationSeconds")
    if seconds:
        try:
            return max(0, int(seconds))
        except (TypeError, ValueError):
            pass
    start = stage.get("startTime")
    end = stage.get("endTime")
    if isinstance(start, str) and isinstance(end, str):
        try:
            start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
            return max(0, int((end_dt - start_dt).total_seconds()))
        except (ValueError, TypeError):
            pass
    return 0


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
        payload = _point_payload(session)
        stages = (
            payload.get("stages")
            or payload.get("sleepStages")
            or (session.get("value", {}) or {}).get("stages")
            or []
        )

        if stages:
            s_deep = s_rem = s_light = s_awake = 0
            for stage in stages:
                seconds = _stage_seconds(stage)
                stage_type = (
                    stage.get("type") or stage.get("stage") or stage.get("sleepStageType") or ""
                ).upper()
                if stage_type in ("DEEP", "DEEP_SLEEP"):
                    s_deep += seconds
                elif stage_type in ("REM", "REM_SLEEP"):
                    s_rem += seconds
                elif stage_type in ("AWAKE", "AWAKE_SLEEP", "WAKE"):
                    s_awake += seconds
                else:
                    # LIGHT / ASLEEP / type inconnu → sommeil léger.
                    s_light += seconds
            deep_sec += s_deep
            rem_sec += s_rem
            light_sec += s_light
            awake_sec += s_awake
            total_sleep_sec += s_deep + s_rem + s_light
        else:
            # Pas de stades : durée totale de la session via l'interval.
            interval = payload.get("interval") or {}
            start = interval.get("startTime") or session.get("startTime")
            end = interval.get("endTime") or session.get("endTime")
            if isinstance(start, str) and isinstance(end, str):
                try:
                    start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
                    end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
                    session_sec = max(0, int((end_dt - start_dt).total_seconds()))
                    total_sleep_sec += session_sec
                    light_sec += session_sec
                except (ValueError, TypeError):
                    pass

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

    # Diagnostic : un legacyUserId présent confirme que les données Fitbit
    # sont bien rattachées au compte Google Health.
    try:
        identity = client.get_identity()
        legacy_id = identity.get("legacyUserId") or identity.get("legacy_user_id")
        if legacy_id:
            log.info("Google Health : compte Fitbit lié (legacyUserId=%s).", legacy_id)
        else:
            log.warning(
                "Google Health : pas de legacyUserId dans l'identité — les données "
                "Fitbit ne sont probablement pas migrées vers ce compte Google. "
                "Identité reçue : %s",
                json.dumps(identity)[:300],
            )
    except Exception:  # noqa: BLE001
        log.warning("Google Health : lecture de l'identité échouée (best-effort).", exc_info=True)

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
