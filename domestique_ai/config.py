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


def _get_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def get_hr_rest() -> float | None:
    """Fréquence cardiaque de repos en bpm. Optionnel — requis pour le scoring HR."""
    return _get_optional_float("STRAVA_HR_REST")


def get_hr_max() -> float | None:
    """Fréquence cardiaque maximale en bpm. Optionnel — requis pour le scoring HR."""
    return _get_optional_float("STRAVA_HR_MAX")


def get_sex() -> str:
    """Sexe ('M' ou 'F') pour les coefficients TRIMP. 'M' par défaut."""
    return os.getenv("STRAVA_SEX", "M").strip().upper()[:1] or "M"


def get_lthr_pct() -> float:
    """Fraction de la HRR considérée comme seuil lactique. 0.88 par défaut."""
    raw = os.getenv("STRAVA_LTHR_PCT")
    if not raw:
        return 0.88
    try:
        value = float(raw)
    except ValueError:
        return 0.88
    return value if 0.5 <= value <= 1.0 else 0.88


def get_strava_credentials() -> tuple[str | None, str | None, str]:
    """Retourne (client_id, client_secret, redirect_uri) depuis .env."""
    return (
        os.getenv("STRAVA_CLIENT_ID"),
        os.getenv("STRAVA_CLIENT_SECRET"),
        os.getenv("STRAVA_REDIRECT_URI", "http://localhost/exchange_token"),
    )


def get_mistral_api_key() -> str | None:
    return os.getenv("MISTRAL_API_KEY")
