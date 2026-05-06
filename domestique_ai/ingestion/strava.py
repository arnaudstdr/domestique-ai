"""
Client Strava OAuth2 + persistance SQLite.

Gère :
- l'autorisation OAuth2 (URL d'auth, échange code → tokens),
- le rafraîchissement automatique du token expiré,
- la récupération paginée des activités,
- la sauvegarde idempotente dans SQLite (clé unique sur strava_id).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from domestique_ai.config import (
    get_db_path,
    get_hr_max,
    get_hr_rest,
    get_tokens_path,
)
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_hr_zones,
    compute_training_load,
)

STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
STRAVA_OAUTH_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_OAUTH_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"

_AUTH_ERROR_MSG = "Token expiré ou invalide (HTTP 401)."


class StravaAuthError(RuntimeError):
    """Erreur d'authentification ou de rafraîchissement de token Strava."""


class StravaClient:
    """Client pour l'API Strava avec gestion automatique du refresh token."""

    def __init__(self, access_token: str, refresh_token: str | None = None,
                 expires_at: int | None = None, client_id: str | None = None,
                 client_secret: str | None = None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at or 0
        self.client_id = client_id
        self.client_secret = client_secret

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @classmethod
    def from_tokens_file(cls, client_id: str, client_secret: str,
                         tokens_path: Path | None = None) -> StravaClient:
        """Recharge un client depuis le fichier local de tokens, refresh si expiré."""
        path = tokens_path or get_tokens_path()
        if not path.exists():
            raise StravaAuthError(
                f"Fichier de tokens introuvable: {path}. "
                "Lancez d'abord `python -m domestique_ai.ingestion.strava_oauth_flow`."
            )
        data = json.loads(path.read_text())
        client = cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at", 0),
            client_id=client_id,
            client_secret=client_secret,
        )
        if client.expires_at <= int(time.time()) + 60:
            client.refresh_access_token()
            client.save_tokens(path)
        return client

    def save_tokens(self, path: Path | None = None) -> None:
        """Persiste les tokens courants sur disque."""
        path = path or get_tokens_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }, indent=2))

    def refresh_access_token(self) -> None:
        """Rafraîchit l'access_token via le refresh_token. Met à jour les attributs en place."""
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise StravaAuthError(
                "client_id, client_secret et refresh_token sont requis pour le refresh."
            )
        response = requests.post(
            STRAVA_OAUTH_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.expires_at = data.get("expires_at", 0)

    def fetch_athlete(self) -> dict[str, Any]:
        """Récupère le profil de l'athlète authentifié (`/athlete`).

        Respecte 401 (StravaAuthError) et 429 (Retry-After + retry).
        """
        url = f"{STRAVA_API_BASE_URL}/athlete"
        while True:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 401:
                raise StravaAuthError(_AUTH_ERROR_MSG)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()

    def fetch_activity_streams(
        self, activity_id: int,
    ) -> tuple[list[float], list[float]] | None:
        """
        Récupère les streams `heartrate` et `time` d'une activité.

        Retourne (heartrate, time) en secondes et bpm.
        Retourne None si l'activité n'a pas de capteur HR (404 ou stream absent).
        Respecte le 429 Retry-After (un seul retry, suffisant pour les pics
        ponctuels — un backfill long appellera la fonction en boucle et
        réapplique le délai naturellement à chaque appel).
        """
        url = f"{STRAVA_API_BASE_URL}/activities/{activity_id}/streams"
        params = {"keys": "heartrate,time", "key_by_type": "true"}
        while True:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            if response.status_code == 401:
                raise StravaAuthError(_AUTH_ERROR_MSG)
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            hr = data.get("heartrate", {}).get("data")
            ts = data.get("time", {}).get("data")
            if not hr or not ts:
                return None
            return hr, ts

    def fetch_streams_full(
        self, activity_id: int, keys: list[str],
    ) -> dict[str, list] | None:
        """
        Récupère un sous-ensemble configurable de streams pour une activité.

        keys : liste parmi `time, latlng, altitude, heartrate, cadence, watts,
            velocity_smooth, distance, temp, moving, grade_smooth`.
        Retourne un dict `{key: data}` (les clés absentes côté Strava sont
        omises). Retourne None si l'activité n'expose aucun de ces streams
        (404 ou activité sans capteur).
        """
        url = f"{STRAVA_API_BASE_URL}/activities/{activity_id}/streams"
        params = {"keys": ",".join(keys), "key_by_type": "true"}
        while True:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            if response.status_code == 401:
                raise StravaAuthError(_AUTH_ERROR_MSG)
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            result: dict[str, list] = {}
            for key in keys:
                stream = data.get(key)
                if stream and stream.get("data"):
                    result[key] = stream["data"]
            return result or None

    def fetch_activity_summary(self, activity_id: int) -> dict[str, Any] | None:
        """
        Récupère le détail complet d'une activité (GET /activities/{id}).

        Inclut nom, type, splits, lieu de départ, etc. Retourne None si 404.
        """
        url = f"{STRAVA_API_BASE_URL}/activities/{activity_id}"
        while True:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 401:
                raise StravaAuthError(_AUTH_ERROR_MSG)
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()

    def fetch_activities(self, after: int | None = None,
                         per_page: int = 200) -> list[dict[str, Any]]:
        """
        Récupère toutes les activités paginées.

        after : timestamp epoch — ne renvoie que les activités après cette date (ingestion incrémentale).
        """
        url = f"{STRAVA_API_BASE_URL}/athlete/activities"
        all_activities: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if after is not None:
                params["after"] = after
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            if response.status_code == 401:
                raise StravaAuthError(_AUTH_ERROR_MSG)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            all_activities.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return all_activities

    def extract_activity_data(self, activity: dict[str, Any]) -> dict[str, Any]:
        """Extrait les champs utiles d'une activité Strava brute."""
        return {
            "id": activity.get("id"),
            "date": activity.get("start_date"),
            "duration": activity.get("elapsed_time"),
            "avg_heart_rate": activity.get("average_heartrate"),
            "max_heart_rate": activity.get("max_heartrate"),
            "avg_power": activity.get("average_watts"),
            "elevation_gain": activity.get("total_elevation_gain"),
            "distance": activity.get("distance"),
            # `sport_type` est le champ moderne (Ride, VirtualRide, MountainBikeRide,
            # Walk, Swim, …). `type` est l'ancien — fallback de robustesse.
            "sport_type": activity.get("sport_type") or activity.get("type"),
        }

    @staticmethod
    def get_authorization_url(client_id: str, redirect_uri: str,
                              scope: str = "activity:read_all") -> str:
        """URL OAuth2 à ouvrir dans le navigateur pour obtenir le code d'autorisation."""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "approval_prompt": "auto",
        }
        return f"{STRAVA_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    @staticmethod
    def exchange_code_for_token(client_id: str, client_secret: str, code: str,
                                redirect_uri: str) -> dict[str, Any]:
        """Échange le code d'autorisation contre access_token + refresh_token + expires_at."""
        response = requests.post(
            STRAVA_OAUTH_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str,
                   ddl: str) -> None:
    """Ajoute une colonne si absente. Migration douce SQLite."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: Path | None = None) -> None:
    """Crée la table `activities` et applique les migrations idempotentes."""
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strava_id INTEGER UNIQUE,
                date TEXT,
                duration INTEGER,
                avg_heart_rate REAL,
                max_heart_rate REAL,
                avg_power REAL,
                elevation_gain REAL,
                distance REAL,
                training_load REAL,
                hr_z1_time REAL,
                hr_z2_time REAL,
                hr_z3_time REAL,
                hr_z4_time REAL,
                hr_z5_time REAL,
                sport_type TEXT
            )
        """)
        _ensure_column(conn, "activities", "max_heart_rate", "REAL")
        for zone in ("hr_z1_time", "hr_z2_time", "hr_z3_time",
                     "hr_z4_time", "hr_z5_time"):
            _ensure_column(conn, "activities", zone, "REAL")
        _ensure_column(conn, "activities", "sport_type", "TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_session "
            "ON conversations(session_id, id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_history (
                date TEXT PRIMARY KEY,
                weight REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS morning_metrics (
                date TEXT PRIMARY KEY,
                hrv_ms REAL,
                resting_hr REAL,
                sleep_hours REAL,
                sleep_score INTEGER,
                stress_score INTEGER,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                target_date TEXT,
                target_event_type TEXT,
                sessions_per_week INTEGER,
                weeks INTEGER,
                payload TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_training_plans_created "
            "ON training_plans(created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def save_activity(activity: dict[str, Any], db_path: Path | None = None,
                  ftp: float | None = None,
                  hr_zones: dict[str, float] | None = None) -> bool:
    """
    Sauvegarde une activité. Calcule la charge d'entraînement si absente
    (hr-TSS si HR configurée, sinon TSS puissance, sinon 0).
    Retourne True si insérée, False si doublon (idempotence sur strava_id).

    hr_zones : dict {"z1": secondes, ..., "z5": secondes} si les streams HR
    ont déjà été calculés en amont. None laisse les colonnes hr_zN_time à NULL
    (signal pour le backfill).
    """
    strava_id = activity.get("id")
    if strava_id is None:
        return False
    path = Path(db_path) if db_path else get_db_path()

    tss = activity.get("training_load")
    if tss is None:
        try:
            duration = int(activity.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            avg_power = float(activity.get("avg_power") or 0.0)
        except (TypeError, ValueError):
            avg_power = 0.0
        try:
            avg_hr = float(activity.get("avg_heart_rate") or 0.0)
        except (TypeError, ValueError):
            avg_hr = 0.0
        tss = compute_training_load(
            duration_sec=duration,
            avg_hr=avg_hr or None,
            avg_power=avg_power or None,
            ftp=ftp,
        )

    zone_values = (
        tuple(hr_zones.get(key) for key in HR_ZONE_KEYS)
        if hr_zones is not None else (None,) * 5
    )

    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute("SELECT 1 FROM activities WHERE strava_id = ?", (strava_id,))
        if cursor.fetchone():
            return False
        conn.execute("""
            INSERT INTO activities (
                strava_id, date, duration, avg_heart_rate, max_heart_rate,
                avg_power, elevation_gain, distance, training_load,
                hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time,
                sport_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            strava_id,
            activity.get("date"),
            activity.get("duration"),
            activity.get("avg_heart_rate"),
            activity.get("max_heart_rate"),
            activity.get("avg_power"),
            activity.get("elevation_gain"),
            activity.get("distance"),
            tss,
            *zone_values,
            activity.get("sport_type"),
        ))
        conn.commit()
        return True
    finally:
        conn.close()


def snapshot_athlete_weight(client: StravaClient,
                            db_path: Path | None = None,
                            today: str | None = None) -> bool:
    """
    Enregistre le poids actuel de l'athlète Strava avec la date du jour.

    Idempotent : si une ligne existe déjà pour la date, elle est mise à jour
    avec la dernière valeur connue (utile si l'utilisateur ajuste son poids
    sur Strava et resynchronise dans la foulée).
    Retourne True si une ligne a été insérée/mise à jour, False si Strava ne
    fournit pas de poids (champ vide ou nul côté profil).
    """
    init_db(db_path)
    path = Path(db_path) if db_path else get_db_path()
    athlete = client.fetch_athlete()
    weight = athlete.get("weight")
    if not weight or weight <= 0:
        return False
    day = today or dt.date.today().isoformat()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO weight_history(date, weight) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET weight = excluded.weight",
            (day, float(weight)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def sync_activities(client: StravaClient, after: int | None = None) -> int:
    """Récupère et sauvegarde les nouvelles activités. Retourne le nombre d'insertions.

    Si HRrepos / HRmax sont configurés, télécharge aussi les streams HR
    pour ventiler chaque activité dans les 5 zones %HRR (1 appel API
    supplémentaire par activité avec capteur HR).

    Snapshot également le poids courant de l'athlète (`weight_history`) —
    silencieux si Strava ne renvoie pas de poids.
    """
    init_db()
    activities = client.fetch_activities(after=after)
    hr_rest = get_hr_rest()
    hr_max = get_hr_max()
    zone_params: tuple[float, float] | None = (
        (float(hr_rest), float(hr_max))
        if hr_rest and hr_max and hr_max > hr_rest
        else None
    )

    inserted = 0
    for raw in activities:
        data = client.extract_activity_data(raw)
        zones: dict[str, float] | None = None
        if zone_params and data.get("avg_heart_rate") and data.get("id"):
            streams = client.fetch_activity_streams(data["id"])
            if streams is not None:
                hr_stream, time_stream = streams
                zones = calculate_hr_zones(hr_stream, time_stream, *zone_params)
        if save_activity(data, hr_zones=zones):
            inserted += 1

    snapshot_athlete_weight(client)
    return inserted


def backfill_hr_zones(client: StravaClient,
                      db_path: Path | None = None) -> int:
    """
    Calcule rétroactivement les zones HR pour les activités déjà en base
    qui ont une avg_heart_rate mais pas encore de hr_z1_time.

    Idempotent : seules les lignes avec `hr_z1_time IS NULL` sont traitées,
    donc relançable sans risque si interrompu (429 prolongé, déconnexion).
    Coûte 1 appel API par activité — Strava limite à 100 req / 15 min.

    Retourne le nombre de lignes effectivement mises à jour.
    """
    init_db(db_path)
    path = Path(db_path) if db_path else get_db_path()
    hr_rest = get_hr_rest()
    hr_max = get_hr_max()
    if not (hr_rest and hr_max and hr_max > hr_rest):
        raise RuntimeError(
            "STRAVA_HR_REST et STRAVA_HR_MAX doivent être configurés "
            "pour calculer les zones HR."
        )

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT strava_id FROM activities "
            "WHERE avg_heart_rate IS NOT NULL AND hr_z1_time IS NULL"
        ).fetchall()
    finally:
        conn.close()

    updated = 0
    for (strava_id,) in rows:
        if strava_id is None:
            continue
        streams = client.fetch_activity_streams(strava_id)
        if streams is None:
            continue
        hr_stream, time_stream = streams
        zones = calculate_hr_zones(hr_stream, time_stream, hr_rest, hr_max)
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "UPDATE activities SET "
                "hr_z1_time = ?, hr_z2_time = ?, hr_z3_time = ?, "
                "hr_z4_time = ?, hr_z5_time = ? WHERE strava_id = ?",
                (
                    *(zones[key] for key in HR_ZONE_KEYS),
                    strava_id,
                ),
            )
            conn.commit()
            updated += 1
        finally:
            conn.close()
    return updated


def backfill_sport_types(client: StravaClient,
                         db_path: Path | None = None) -> int:
    """
    Complète la colonne `sport_type` sur les activités existantes en base
    qui ne l'ont pas (lignes ajoutées avant l'introduction du champ).

    Idempotent : ne fait l'appel API que s'il reste au moins une activité
    sans sport_type. Met à jour uniquement les lignes WHERE sport_type IS NULL.
    Retourne le nombre de lignes mises à jour.
    """
    init_db(db_path)
    path = Path(db_path) if db_path else get_db_path()
    conn = sqlite3.connect(path)
    try:
        missing = {
            row[0]
            for row in conn.execute(
                "SELECT strava_id FROM activities WHERE sport_type IS NULL"
            )
        }
    finally:
        conn.close()

    if not missing:
        return 0

    activities = client.fetch_activities()
    conn = sqlite3.connect(path)
    try:
        updated = 0
        for raw in activities:
            data = client.extract_activity_data(raw)
            strava_id = data.get("id")
            if strava_id not in missing:
                continue
            sport_type = data.get("sport_type")
            if not sport_type:
                continue
            conn.execute(
                "UPDATE activities SET sport_type = ? WHERE strava_id = ?",
                (sport_type, strava_id),
            )
            updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


def backfill_activity_fields(client: StravaClient,
                             db_path: Path | None = None) -> int:
    """
    Re-fetch tout l'historique Strava et complète les colonnes manquantes
    (max_heart_rate notamment) sur les activités déjà en base.

    Idempotent : ne touche aux lignes que si la valeur change.
    Retourne le nombre de lignes mises à jour.
    """
    init_db(db_path)
    path = Path(db_path) if db_path else get_db_path()
    activities = client.fetch_activities()
    conn = sqlite3.connect(path)
    try:
        existing = {
            row[0]
            for row in conn.execute("SELECT strava_id FROM activities")
        }
        updated = 0
        for raw in activities:
            data = client.extract_activity_data(raw)
            strava_id = data.get("id")
            if strava_id is None or strava_id not in existing:
                continue
            cursor = conn.execute(
                "SELECT max_heart_rate FROM activities WHERE strava_id = ?",
                (strava_id,),
            )
            row = cursor.fetchone()
            current_max_hr = row[0] if row else None
            new_max_hr = data.get("max_heart_rate")
            if new_max_hr is not None and current_max_hr != new_max_hr:
                conn.execute(
                    "UPDATE activities SET max_heart_rate = ? "
                    "WHERE strava_id = ?",
                    (new_max_hr, strava_id),
                )
                updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()
