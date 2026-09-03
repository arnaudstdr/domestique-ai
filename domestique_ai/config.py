"""Configuration centralisée — chemins de données, FTP utilisateur, secrets via .env.

Le profil athlète (`data/profile.yaml`) prend la priorité sur les variables
d'environnement pour FTP, HR repos, HR max, sexe et %LTHR. Les getters lisent
le YAML d'abord (via cache mémoire indexé sur `mtime`), tombent sur `.env`
ensuite, puis sur le défaut.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv()

if TYPE_CHECKING:
    from domestique_ai.llm.profile import Profile

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    """Chemin vers la base SQLite des activités. Override possible via DOMESTIQUE_AI_DB_PATH."""
    custom = os.getenv("DOMESTIQUE_AI_DB_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "strava_activities.db"


def get_tokens_path() -> Path:
    """Chemin du fichier de stockage local des tokens Strava (jamais commité)."""
    return REPO_ROOT / "data" / ".strava_tokens.json"


def get_platform_db_path() -> Path:
    """Chemin de la DB plateforme (identité : comptes, sessions, invitations).

    Séparée de la DB activités (qui deviendra « une par athlète »). Override via
    DOMESTIQUE_AI_PLATFORM_DB_PATH.
    """
    custom = os.getenv("DOMESTIQUE_AI_PLATFORM_DB_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "platform.db"


def get_athletes_root() -> Path:
    """Racine des espaces de données par athlète (comptes non-bootstrap).

    Chaque athlète non-propriétaire a son dossier ``<root>/<public_id>/`` (DB,
    tokens Strava, YAML). Le propriétaire (bootstrap) garde ses données legacy
    en place. Override via DOMESTIQUE_AI_ATHLETES_ROOT.
    """
    custom = os.getenv("DOMESTIQUE_AI_ATHLETES_ROOT")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "athletes"


def get_session_secret() -> bytes:
    """Secret (pepper) pour le HMAC des tokens de session/invitation.

    Priorité : DOMESTIQUE_AI_SESSION_SECRET > dérivé de DOMESTIQUE_AI_API_TOKEN
    (évite d'imposer une nouvelle variable obligatoire en prod) > constante de dev
    (avec warning). Retourne des bytes prêts pour ``hmac.new``.
    """
    raw = os.getenv("DOMESTIQUE_AI_SESSION_SECRET")
    if raw and raw.strip():
        return raw.strip().encode("utf-8")
    api_token = os.getenv("DOMESTIQUE_AI_API_TOKEN")
    if api_token and api_token.strip():
        return hashlib.sha256(
            b"domestique-ai-session-v1:" + api_token.strip().encode("utf-8")
        ).digest()
    logger.warning(
        "DOMESTIQUE_AI_SESSION_SECRET et DOMESTIQUE_AI_API_TOKEN absents — "
        "secret de session dérivé d'une constante de dev. NE PAS utiliser en prod."
    )
    return b"domestique-ai-dev-session-secret"


def get_profile_path() -> Path:
    """Chemin du YAML profil athlète. Override via DOMESTIQUE_AI_PROFILE_PATH."""
    custom = os.getenv("DOMESTIQUE_AI_PROFILE_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "profile.yaml"


# ---------------------------------------------------------------------------
# Cache profil (mtime-based, sans daemon).
#
# Le coût d'un appel `load_profile` est faible (YAML court), mais on évite la
# relecture systématique à chaque getter pour garder les chauds chemins du
# calcul de charge rapides. Le cache est invalidé si :
#   - le mtime du fichier a changé (édition externe),
#   - le chemin résolu via env var a changé (utile pour les tests qui
#     `monkeypatch.setenv("DOMESTIQUE_AI_PROFILE_PATH", ...)`),
#   - `invalidate_profile_cache()` est appelée explicitement (par le router
#     après PUT).
# ---------------------------------------------------------------------------

_profile_cache_lock = threading.Lock()
_profile_cache: dict[str, object] = {
    "path": None,  # str | None
    "mtime_ns": None,  # int | None
    "profile": None,  # Profile | None
}


def invalidate_profile_cache() -> None:
    """Force la relecture du profil au prochain accès."""
    with _profile_cache_lock:
        _profile_cache["path"] = None
        _profile_cache["mtime_ns"] = None
        _profile_cache["profile"] = None


def _profile_or_none() -> Profile | None:
    """Renvoie le profil persisté (avec cache mtime) ou ``None`` si fichier absent."""
    # Import local pour éviter le cycle config <-> llm.profile.
    from domestique_ai.llm.profile import ProfileError, load_profile

    path = get_profile_path()
    path_str = str(path)
    mtime_ns: int | None = None
    if path.exists():
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None

    with _profile_cache_lock:
        if _profile_cache["path"] == path_str and _profile_cache["mtime_ns"] == mtime_ns:
            return _profile_cache["profile"]  # type: ignore[return-value]

        try:
            profile = load_profile(path)
        except ProfileError:
            # YAML invalide : on log côté caller éventuellement, ici on évite
            # de planter la chaîne de calcul. Cache la décision pour ne pas
            # relire à chaque appel.
            profile = None

        _profile_cache["path"] = path_str
        _profile_cache["mtime_ns"] = mtime_ns
        _profile_cache["profile"] = profile
        return profile


def get_ftp() -> float:
    """FTP en watts. Priorité : profile.yaml > STRAVA_FTP > 250.0."""
    profile = _profile_or_none()
    if profile is not None and profile.ftp is not None:
        return float(profile.ftp)
    raw = os.getenv("STRAVA_FTP")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "STRAVA_FTP=%r n'est pas un float valide — fallback à 250.0 W "
                "(le calcul TSS power utilisera cette valeur par défaut).",
                raw,
            )
    return 250.0


def _get_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r n'est pas un float valide — ignoré (fonctionnalité dégradée).",
            name,
            raw,
        )
        return None
    if value <= 0:
        logger.warning(
            "%s=%r doit être strictement positif — ignoré.",
            name,
            raw,
        )
        return None
    return value


def get_hr_rest() -> float | None:
    """FC repos. Priorité : profile.yaml > STRAVA_HR_REST > None."""
    profile = _profile_or_none()
    if profile is not None and profile.hr_rest is not None:
        return float(profile.hr_rest)
    return _get_optional_float("STRAVA_HR_REST")


def get_hr_max() -> float | None:
    """FC max. Priorité : profile.yaml > STRAVA_HR_MAX > None."""
    profile = _profile_or_none()
    if profile is not None and profile.hr_max is not None:
        return float(profile.hr_max)
    return _get_optional_float("STRAVA_HR_MAX")


def get_sex() -> str:
    """Sexe ('M' ou 'F') pour les coefficients TRIMP. Priorité : profile.yaml > STRAVA_SEX > 'M'."""
    profile = _profile_or_none()
    if profile is not None and profile.sex:
        return profile.sex.upper()[:1] or "M"
    return os.getenv("STRAVA_SEX", "M").strip().upper()[:1] or "M"


def get_lthr_pct() -> float:
    """Fraction HRR considérée comme seuil lactique. Priorité : profile.yaml > STRAVA_LTHR_PCT > 0.88."""
    profile = _profile_or_none()
    if profile is not None:
        value = profile.lthr_pct
        if 0.5 <= value <= 1.0:
            return value
    raw = os.getenv("STRAVA_LTHR_PCT")
    if not raw:
        return 0.88
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "STRAVA_LTHR_PCT=%r n'est pas un float valide — fallback à 0.88.",
            raw,
        )
        return 0.88
    if not (0.5 <= value <= 1.0):
        logger.warning(
            "STRAVA_LTHR_PCT=%r hors bornes (attendu entre 0.5 et 1.0) — fallback à 0.88.",
            raw,
        )
        return 0.88
    return value


def get_strava_credentials() -> tuple[str | None, str | None, str]:
    """Retourne (client_id, client_secret, redirect_uri) depuis .env.

    En multi-tenant, ``STRAVA_REDIRECT_URI`` doit pointer sur le callback web
    ``<host>/api/strava/callback`` (et être autorisé dans l'app Strava).
    """
    return (
        os.getenv("STRAVA_CLIENT_ID"),
        os.getenv("STRAVA_CLIENT_SECRET"),
        os.getenv("STRAVA_REDIRECT_URI", "http://localhost/exchange_token"),
    )


def get_app_base_url() -> str:
    """Base URL publique de l'app, pour la redirection post-OAuth.

    Override via DOMESTIQUE_AI_APP_BASE_URL. Défaut ``""`` → redirection
    relative same-origin (``/?strava=connected``), suffisant quand le backend
    sert le frontend. Sans slash final.
    """
    return os.getenv("DOMESTIQUE_AI_APP_BASE_URL", "").rstrip("/")


def get_objective_path() -> Path:
    """Chemin vers le fichier YAML d'objectif d'entraînement (gitignoré).

    Override possible via DOMESTIQUE_AI_OBJECTIVE_PATH (utile pour les tests).
    """
    custom = os.getenv("DOMESTIQUE_AI_OBJECTIVE_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "objective.yaml"


def get_availability_path() -> Path:
    """Chemin vers le fichier YAML de disponibilité hebdomadaire (gitignoré).

    Override possible via DOMESTIQUE_AI_AVAILABILITY_PATH (utile pour les tests).
    """
    custom = os.getenv("DOMESTIQUE_AI_AVAILABILITY_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "availability.yaml"


def get_garmin_credentials() -> tuple[str | None, str | None]:
    """Identifiants Garmin Connect lus depuis l'env (.env).

    Retourne ``(email, password)``. Les deux sont ``None`` si non configurés —
    auquel cas le push vers Connect est désactivé côté UI.
    """
    return (
        os.getenv("GARMIN_EMAIL") or None,
        os.getenv("GARMIN_PASSWORD") or None,
    )


def get_garmin_token_dir() -> Path:
    """Répertoire où ``garth`` cache les tokens Garmin Connect.

    Override possible via ``GARMIN_TOKEN_DIR`` (utile pour les tests). Défaut :
    ``data/.garmin_tokens`` (gitignoré comme le reste de ``data/``).
    """
    custom = os.getenv("GARMIN_TOKEN_DIR")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / ".garmin_tokens"


def get_ollama_model() -> str:
    """Modèle Ollama utilisé par le coach. Override via OLLAMA_MODEL."""
    return os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud")


def get_ollama_host() -> str | None:
    """Host Ollama. None = SDK utilise sa valeur par défaut (http://localhost:11434)."""
    return os.getenv("OLLAMA_HOST") or None


def get_api_token() -> str | None:
    """Token Bearer requis pour les endpoints ``/api/*`` (sauf ``/api/health``).

    Lu depuis ``DOMESTIQUE_AI_API_TOKEN``. ``None`` → auth désactivée
    (mode dev local). En prod, doit être un secret cryptographique
    (ex. ``openssl rand -hex 32``).
    """
    raw = os.getenv("DOMESTIQUE_AI_API_TOKEN")
    return raw.strip() if raw and raw.strip() else None
