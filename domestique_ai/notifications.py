"""Notifications push (Pushover) — palier 4 de la proactivité du coach.

Module best-effort : aucun appel d'API métier ne doit jamais planter à
cause d'une notif. Toutes les fonctions publiques capturent leurs
exceptions et loggent en warning.

Configuration via variables d'environnement :
- ``PUSHOVER_USER_KEY`` : clé utilisateur (générée sur pushover.net).
- ``PUSHOVER_APP_TOKEN`` : token d'application (créé sur pushover.net/apps).
- ``PUSHOVER_DEVICE`` : optionnel, cible un device précis (sinon tous).
- ``PUSHOVER_PRIORITY_DEFAULT`` : optionnel, priorité par défaut (-2 à 2,
  défaut 0).

Si ``PUSHOVER_USER_KEY`` ou ``PUSHOVER_APP_TOKEN`` est absent, toutes les
fonctions sont silencieusement no-op — pratique en dev / pour désactiver
sans changer le code.
"""

from __future__ import annotations

import os

import requests

from domestique_ai.api.logging import get_logger

log = get_logger("notifications")

_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
_HTTP_TIMEOUT_SEC = 10


def _credentials() -> tuple[str, str] | None:
    """Retourne ``(user_key, app_token)`` si configurés, sinon ``None``."""
    user = os.getenv("PUSHOVER_USER_KEY")
    token = os.getenv("PUSHOVER_APP_TOKEN")
    if not user or not token:
        return None
    return user, token


def _default_priority() -> int:
    raw = os.getenv("PUSHOVER_PRIORITY_DEFAULT")
    if not raw:
        return 0
    try:
        v = int(raw)
    except ValueError:
        return 0
    return max(-2, min(2, v))


def send_pushover(
    title: str,
    message: str,
    priority: int | None = None,
) -> bool:
    """Envoie une notif Pushover. Retourne ``True`` si envoyée, ``False`` sinon.

    Best-effort : si la configuration est absente ou si l'appel HTTP échoue,
    on log un warning et on retourne ``False`` — l'appelant continue.
    """
    creds = _credentials()
    if creds is None:
        log.debug("Pushover non configuré — notif ignorée (%r).", title)
        return False
    user, token = creds
    payload: dict[str, str | int] = {
        "user": user,
        "token": token,
        "title": title,
        "message": message,
        "priority": priority if priority is not None else _default_priority(),
    }
    device = os.getenv("PUSHOVER_DEVICE")
    if device:
        payload["device"] = device

    try:
        response = requests.post(_PUSHOVER_URL, data=payload, timeout=_HTTP_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.warning("Pushover : échec d'envoi (%s) — %s", type(exc).__name__, exc)
        return False

    if response.status_code != 200:
        log.warning(
            "Pushover : réponse non-200 (%d) — %s",
            response.status_code,
            response.text[:200],
        )
        return False
    return True


def notify_sync_completed(inserted: int, *, user: dict | None = None) -> bool:
    """Notifie l'utilisateur quand une sync a importé des activités.

    No-op si ``inserted <= 0`` (on ne spamme pas sur les sync à vide). Si
    ``user`` est fourni (multi-athlète), le nom est préfixé au message.
    """
    if inserted <= 0:
        return False
    try:
        prefix = ""
        if user and user.get("display_name"):
            prefix = f"{user['display_name']} : "
        if inserted == 1:
            title = "Nouvelle activité"
            message = f"{prefix}Une nouvelle activité a été ingérée."
        else:
            title = "Nouvelles activités"
            message = f"{prefix}{inserted} nouvelles activités ont été ingérées."
        return send_pushover(title, message)
    except Exception:  # noqa: BLE001 — notif ne doit jamais casser l'appelant
        log.exception("notify_sync_completed : exception non gérée")
        return False
