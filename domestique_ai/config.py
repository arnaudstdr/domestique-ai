"""Configuration centralisée — chemins de données, FTP utilisateur, secrets via .env.

Le profil athlète (`data/profile.yaml`) prend la priorité sur les variables
d'environnement pour FTP, HR repos, HR max, sexe et %LTHR. Les getters lisent
le YAML d'abord (via cache mémoire indexé sur `mtime`), tombent sur `.env`
ensuite, puis sur le défaut.
"""

from __future__ import annotations

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


def get_db_path() -> Path:
    """Chemin vers la base SQLite des activités. Override possible via DOMESTIQUE_AI_DB_PATH."""
    custom = os.getenv("DOMESTIQUE_AI_DB_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    return REPO_ROOT / "data" / "strava_activities.db"


def get_tokens_path() -> Path:
    """Chemin du fichier de stockage local des tokens Strava (jamais commité)."""
    return REPO_ROOT / "data" / ".strava_tokens.json"


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
    "path": None,        # str | None
    "mtime_ns": None,    # int | None
    "profile": None,     # Profile | None
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
        if (
            _profile_cache["path"] == path_str
            and _profile_cache["mtime_ns"] == mtime_ns
        ):
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
            pass
    return 250.0


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
        return 0.88
    return value if 0.5 <= value <= 1.0 else 0.88


def get_strava_credentials() -> tuple[str | None, str | None, str]:
    """Retourne (client_id, client_secret, redirect_uri) depuis .env."""
    return (
        os.getenv("STRAVA_CLIENT_ID"),
        os.getenv("STRAVA_CLIENT_SECRET"),
        os.getenv("STRAVA_REDIRECT_URI", "http://localhost/exchange_token"),
    )


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
