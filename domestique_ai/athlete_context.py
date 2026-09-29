"""Contexte athlète injectable — découple le moteur de la config globale (.env).

``AthleteContext`` regroupe la config *par athlète* (chemin DB, profil HR/FTP,
chemins YAML). Le moteur (``ingestion/``, ``processing/``) le reçoit
explicitement au lieu de lire les getters globaux de ``domestique_ai.config`` —
ce qui permettra à un même backend de traiter plusieurs athlètes isolés
(cf. ``COACH_APP_DESIGN.md``).

``context_from_env()`` reproduit exactement le comportement mono-utilisateur
actuel en déléguant aux getters de ``config`` : toute la couche
profil YAML > env > défaut, le cache mtime et les validations restent dans
``config``. Ce module ne lit donc **aucune** variable d'environnement
directement — il ne fait que composer les getters.

Le contexte est un **snapshot immuable** : il doit être construit frais à chaque
point d'entrée (requête, sync), jamais mémoïsé, sinon une édition de profil
runtime (``PUT /profile`` → ``invalidate_profile_cache()``) ne serait plus prise
en compte.

La config *applicative partagée* (credentials Garmin, modèle Ollama, token API,
intervalles scheduler) n'est volontairement **pas** dans le contexte : elle ne
varie pas d'un athlète à l'autre.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from domestique_ai.config import (
    get_athletes_root,
    get_availability_path,
    get_db_path,
    get_ftp,
    get_garmin_credentials,
    get_hr_max,
    get_hr_rest,
    get_level,
    get_lthr_pct,
    get_objective_path,
    get_profile_path,
    get_sex,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AthleteContext:
    """Config par athlète — snapshot immuable threadé dans le moteur."""

    db_path: Path
    profile_path: Path
    objective_path: Path
    availability_path: Path
    ftp: float
    hr_rest: float | None
    hr_max: float | None
    sex: str
    lthr_pct: float
    level: str = "intermediate"
    # ``public_id`` vide = bootstrap (données legacy au chemin global). Sert à
    # dériver les chemins de connexion par athlète (tokens Garmin/Google Health).
    public_id: str = ""
    # Credentials Garmin *du compte de l'athlète* (sinon fallback env bootstrap).
    # Ne sortent jamais dans un payload API.
    garmin_email: str | None = None
    garmin_password: str | None = None


def context_from_env() -> AthleteContext:
    """Construit un contexte depuis la config globale (.env + profil YAML).

    Délègue intégralement aux getters de ``config`` : ``context_from_env().ftp``
    vaut ``get_ftp()`` par construction. À appeler frais à chaque point d'entrée.
    """
    garmin_email, garmin_password = get_garmin_credentials()
    return AthleteContext(
        db_path=get_db_path(),
        profile_path=get_profile_path(),
        objective_path=get_objective_path(),
        availability_path=get_availability_path(),
        ftp=get_ftp(),
        hr_rest=get_hr_rest(),
        hr_max=get_hr_max(),
        sex=get_sex(),
        lthr_pct=get_lthr_pct(),
        level=get_level(),
        public_id="",
        garmin_email=garmin_email,
        garmin_password=garmin_password,
    )


def context_for_athlete(user: dict) -> AthleteContext:
    """Contexte de données de l'utilisateur ``user`` (dict de la DB plateforme).

    - **bootstrap** (propriétaire) → ``context_from_env()`` : ses données legacy
      restent en place, zéro migration.
    - **autre** → espace dédié sous ``get_athletes_root()/<public_id>/``. Le profil
      HR/FTP est résolu depuis le ``profile.yaml`` DE CET athlète, avec des défauts
      en dur et **aucun fallback env** — ce qui isole proprement chaque athlète
      (et ferme la fuite env de ``compute_training_load`` côté multi-tenant).
    """
    if user.get("is_bootstrap"):
        return context_from_env()

    # Imports locaux : `load_profile` vit dans llm.profile et l'importer au top
    # créerait un cycle (cf. config._profile_or_none).
    from domestique_ai.llm.profile import load_profile
    from domestique_ai.platform_db import get_user_garmin_credentials

    root = get_athletes_root() / user["public_id"]
    profile_path = root / "profile.yaml"
    profile = load_profile(profile_path)  # Profile | None, ne lit jamais l'env
    user_id = user.get("id")
    garmin_email, garmin_password = (
        get_user_garmin_credentials(user_id) if user_id is not None else (None, None)
    )

    return AthleteContext(
        db_path=root / "strava_activities.db",
        profile_path=profile_path,
        objective_path=root / "objective.yaml",
        availability_path=root / "availability.yaml",
        ftp=float(profile.ftp) if (profile and profile.ftp is not None) else 250.0,
        hr_rest=profile.hr_rest if profile else None,
        hr_max=profile.hr_max if profile else None,
        sex=(profile.sex if profile else None) or "M",
        lthr_pct=profile.lthr_pct if profile else 0.88,
        level=profile.level if profile else "intermediate",
        public_id=user["public_id"],
        garmin_email=garmin_email,
        garmin_password=garmin_password,
    )


def remove_athlete_space(public_id: str) -> None:
    """Supprime l'espace de données disque d'un athlète (best-effort).

    Efface ``data/athletes/<public_id>/`` (base activités, tokens Garmin/Google
    Health, YAML profil/objectif/dispo). No-op si ``public_id`` est vide
    (bootstrap, dont les données legacy ne sont jamais supprimées) ou si le
    dossier n'existe pas. Un échec d'I/O est loggé mais n'interrompt pas
    l'appelant (la suppression du compte plateforme, elle, est déjà faite).
    """
    if not public_id:
        return
    target_dir = get_athletes_root() / public_id
    try:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
    except OSError:  # noqa: BLE001 — best-effort
        logger.warning("Suppression du dossier athlète %s échouée.", target_dir, exc_info=True)
