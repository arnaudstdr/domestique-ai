"""Heartbeat vers Healthchecks.io (dead man's switch).

Module best-effort : l'app push un ping HTTP régulier vers une URL fournie
par Healthchecks.io. Si les pings s'arrêtent (app crash, Pi éteint, réseau
coupé), Healthchecks.io déclenche l'alerte configurée côté leur UI
(Pushover, email, Slack…).

Avantage par rapport à un watchdog local : on détecte aussi les pannes où
le Pi entier est down, ce qu'un check sur ``localhost`` ne verrait jamais.

Configuration via variables d'environnement :
- ``HEALTHCHECKS_PING_URL`` : URL ping fournie par Healthchecks.io après
  création du check (ex. ``https://hc-ping.com/abc-123-def``). Si absente,
  le ping est silencieusement désactivé.
"""

from __future__ import annotations

import os

import requests

from domestique_ai.api.logging import get_logger

log = get_logger("healthcheck")

_HTTP_TIMEOUT_SEC = 10


def _ping_url() -> str | None:
    raw = os.getenv("HEALTHCHECKS_PING_URL")
    if not raw:
        return None
    return raw.strip() or None


def ping_healthcheck() -> bool:
    """Envoie un ping à Healthchecks.io. Retourne ``True`` si 2xx.

    Best-effort : no-op silencieux si non configuré, warning si l'appel
    échoue, et aucune exception ne remonte à l'appelant.
    """
    url = _ping_url()
    if url is None:
        return False
    try:
        response = requests.get(url, timeout=_HTTP_TIMEOUT_SEC)
    except requests.RequestException as exc:
        log.warning(
            "Healthcheck ping : échec réseau (%s) — %s",
            type(exc).__name__,
            exc,
        )
        return False
    if response.status_code >= 400:
        log.warning(
            "Healthcheck ping : réponse non-2xx (%d)", response.status_code
        )
        return False
    return True
