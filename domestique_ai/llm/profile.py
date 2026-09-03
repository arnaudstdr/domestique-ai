"""
Lecture/écriture du profil utilisateur (FTP, HR repos, HR max, sexe, %LTHR).

Fichier YAML simple `data/profile.yaml` (gitignoré), template versionné en
`data/profile.yaml.example`. Pattern miroir de `objectives.py` et
`availability.py`.

Les getters de `domestique_ai.config` lisent ce fichier en priorité avec
fallback `.env` pour rétro-compatibilité. Voir `config._profile_or_none()`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from domestique_ai.config import get_profile_path

VALID_SEX = {"M", "F"}


class ProfileError(ValueError):
    """Erreur de validation ou de lecture du profil utilisateur."""


@dataclass
class Profile:
    """Paramètres physiologiques de l'athlète.

    Tous les champs sont optionnels — le getter retombe sur `.env` puis sur la
    valeur par défaut quand un champ n'est pas défini ici.
    """

    ftp: float | None = None
    hr_rest: float | None = None
    hr_max: float | None = None
    sex: str = "M"
    lthr_pct: float = 0.88

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # On garde sex / lthr_pct même s'ils valent leur défaut (utile pour
        # rendre la config explicite côté YAML).
        return {k: v for k, v in data.items() if v is not None}


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    sex = payload.get("sex", "M")
    sex_upper = sex.strip().upper()[:1] or "M" if isinstance(sex, str) else "M"
    if sex_upper not in VALID_SEX:
        raise ProfileError(f"sex invalide: {sex!r}. Attendu: {sorted(VALID_SEX)}")
    payload["sex"] = sex_upper

    for key in ("ftp", "hr_rest", "hr_max"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"{key} invalide: {value!r}") from exc
        if value <= 0:
            raise ProfileError(f"{key} doit être > 0, reçu {value}")
        payload[key] = value

    lthr_pct = payload.get("lthr_pct", 0.88)
    try:
        lthr_pct = float(lthr_pct)
    except (TypeError, ValueError) as exc:
        raise ProfileError(f"lthr_pct invalide: {lthr_pct!r}") from exc
    if not 0.5 <= lthr_pct <= 1.0:
        raise ProfileError(f"lthr_pct hors borne [0.5, 1.0], reçu {lthr_pct}")
    payload["lthr_pct"] = lthr_pct
    return payload


def load_profile(path: Path | None = None) -> Profile | None:
    """Charge le profil depuis le YAML. Retourne `None` si fichier absent."""
    target = path or get_profile_path()
    if not target.exists():
        return None
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        raise ProfileError(f"Le fichier {target} doit contenir un dictionnaire YAML.")
    payload = _validate(dict(raw))
    return Profile(
        ftp=payload.get("ftp"),
        hr_rest=payload.get("hr_rest"),
        hr_max=payload.get("hr_max"),
        sex=payload.get("sex", "M"),
        lthr_pct=payload.get("lthr_pct", 0.88),
    )


def save_profile(profile: Profile, path: Path | None = None) -> Path:
    """Sérialise le profil au format YAML."""
    target = path or get_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(profile.to_dict(), allow_unicode=True, sort_keys=False))
    return target


__all__ = ["Profile", "ProfileError", "load_profile", "save_profile"]
