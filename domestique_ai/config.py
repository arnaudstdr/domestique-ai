"""Configuration centralisée — chemins de données, FTP utilisateur, secrets via .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent


def get_db_path() -> Path:
    """Chemin vers la base SQLite des activités. Override possible via DOMESTIQUE_AI_DB_PATH."""
    custom = os.getenv("DOMESTIQUE_AI_DB_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "strava_activities.db"


def get_tokens_path() -> Path:
    """Chemin du fichier de stockage local des tokens Strava (jamais commité)."""
    return REPO_ROOT / "data" / ".strava_tokens.json"


def get_ftp() -> float:
    """FTP en watts, lue depuis l'env. 250 par défaut."""
    return float(os.getenv("STRAVA_FTP", "250"))


def get_strava_credentials() -> tuple[str | None, str | None, str]:
    """Retourne (client_id, client_secret, redirect_uri) depuis .env."""
    return (
        os.getenv("STRAVA_CLIENT_ID"),
        os.getenv("STRAVA_CLIENT_SECRET"),
        os.getenv("STRAVA_REDIRECT_URI", "http://localhost/exchange_token"),
    )


def get_mistral_api_key() -> str | None:
    return os.getenv("MISTRAL_API_KEY")
