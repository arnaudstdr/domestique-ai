"""
Client Strava OAuth2 + persistance SQLite.

Gère :
- l'autorisation OAuth2 (URL d'auth, échange code → tokens),
- le rafraîchissement automatique du token expiré,
- la récupération paginée des activités,
- la sauvegarde idempotente dans SQLite (clé unique sur strava_id).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from domestique_ai.config import get_db_path, get_ftp, get_tokens_path
from domestique_ai.processing.analyzer import calculate_tss

STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
STRAVA_OAUTH_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_OAUTH_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"


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
                raise StravaAuthError("Token expiré ou invalide (HTTP 401).")
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
            "avg_power": activity.get("average_watts"),
            "elevation_gain": activity.get("total_elevation_gain"),
            "distance": activity.get("distance"),
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


def init_db(db_path: Path | None = None) -> None:
    """Crée la table `activities` si elle n'existe pas."""
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
                avg_power REAL,
                elevation_gain REAL,
                distance REAL,
                training_load REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_activity(activity: dict[str, Any], db_path: Path | None = None,
                  ftp: float | None = None) -> bool:
    """
    Sauvegarde une activité. Calcule le TSS si absent (à partir de avg_power et FTP).
    Retourne True si insérée, False si doublon (idempotence sur strava_id).
    """
    strava_id = activity.get("id")
    if strava_id is None:
        return False
    path = Path(db_path) if db_path else get_db_path()
    ftp_value = ftp if ftp is not None else get_ftp()

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
        tss = calculate_tss(duration, avg_power, ftp_value)

    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute("SELECT 1 FROM activities WHERE strava_id = ?", (strava_id,))
        if cursor.fetchone():
            return False
        conn.execute("""
            INSERT INTO activities (
                strava_id, date, duration, avg_heart_rate,
                avg_power, elevation_gain, distance, training_load
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            strava_id,
            activity.get("date"),
            activity.get("duration"),
            activity.get("avg_heart_rate"),
            activity.get("avg_power"),
            activity.get("elevation_gain"),
            activity.get("distance"),
            tss,
        ))
        conn.commit()
        return True
    finally:
        conn.close()


def sync_activities(client: StravaClient, after: int | None = None) -> int:
    """Récupère et sauvegarde les nouvelles activités. Retourne le nombre d'insertions."""
    init_db()
    activities = client.fetch_activities(after=after)
    inserted = 0
    for raw in activities:
        data = client.extract_activity_data(raw)
        if save_activity(data):
            inserted += 1
    return inserted
