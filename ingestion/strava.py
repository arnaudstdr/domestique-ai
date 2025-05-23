"""
Module d'ingestion pour la récupération des activités Strava via OAuth2.
Ce module gère l'authentification, la récupération et le stockage local des données d'activités.
"""

import requests
from typing import List, Dict, Any
import sqlite3
import os

# URL de base de l'API Strava
STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
DB_PATH = os.path.join(os.path.dirname(__file__), '../data/strava_activities.db')

class StravaClient:
    """
    Client pour interagir avec l'API Strava.
    Gère l'authentification OAuth2 et la récupération des activités.
    """
    def __init__(self, access_token: str):
        """
        Initialise le client avec un token d'accès OAuth2.
        """
        self.access_token = access_token
        self.headers = {"Authorization": f"Bearer {self.access_token}"}

    def fetch_activities(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste des activités de l'utilisateur connecté.
        Retourne une liste de dictionnaires représentant les activités.
        """
        url = f"{STRAVA_API_BASE_URL}/athlete/activities"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def extract_activity_data(self, activity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les données importantes d'une activité Strava.
        """
        return {
            "date": activity.get("start_date"),
            "duration": activity.get("elapsed_time"),
            "avg_heart_rate": activity.get("average_heartrate"),
            "avg_power": activity.get("average_watts"),
            "elevation_gain": activity.get("total_elevation_gain"),
            "distance": activity.get("distance"),
            # Le TSS n'est pas fourni par Strava, à calculer plus tard
        }

    def get_authorization_url(self, client_id: str, redirect_uri: str, scope: str = "activity:read_all") -> str:
        """
        Génère l'URL d'autorisation OAuth2 pour Strava.
        L'utilisateur doit visiter cette URL pour autoriser l'application et obtenir un code d'autorisation.
        """
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "approval_prompt": "auto"
        }
        from urllib.parse import urlencode
        return f"https://www.strava.com/oauth/authorize?{urlencode(params)}"

    @staticmethod
    def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
        """
        Échange le code d'autorisation contre un token d'accès OAuth2.
        Retourne un dictionnaire contenant access_token, refresh_token, expires_at, etc.
        """
        url = "https://www.strava.com/oauth/token"
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        response = requests.post(url, data=data)
        response.raise_for_status()
        return response.json()

def init_db(db_path: str = DB_PATH):
    """
    Initialise la base SQLite pour stocker les activités Strava.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            duration INTEGER,
            avg_heart_rate REAL,
            avg_power REAL,
            elevation_gain REAL,
            distance REAL,
            training_load REAL
        )
    ''')
    conn.commit()
    conn.close()

def save_activity(activity: dict, db_path: str = DB_PATH):
    """
    Sauvegarde une activité dans la base SQLite.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO activities (date, duration, avg_heart_rate, avg_power, elevation_gain, distance, training_load)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        activity.get('date'),
        activity.get('duration'),
        activity.get('avg_heart_rate'),
        activity.get('avg_power'),
        activity.get('elevation_gain'),
        activity.get('distance'),
        activity.get('training_load')
    ))
    conn.commit()
    conn.close()
