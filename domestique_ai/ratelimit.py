"""Rate limiting in-process (fenêtre glissante) pour les endpoints publics sensibles.

Protège ``/api/auth/signup``, ``/api/auth/forgot-password`` et
``/api/auth/resend-verification`` contre l'abus (création massive de comptes,
spam SMTP, énumération).

Limitations assumées : l'état vit dans le process. En single-worker uvicorn
(cas de l'app) c'est suffisant ; en multi-worker, chaque worker a son propre
compteur. Derrière un reverse proxy, ``request.client.host`` peut être l'IP du
proxy — activer ``DOMESTIQUE_AI_TRUSTED_PROXY=1`` pour que l'IP client soit lue
dans ``X-Forwarded-For`` (premier hop), à ne faire que si le proxy écrase
l'en-tête : sinon il est spoofable en accès direct.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from domestique_ai.config import get_trusted_proxy

_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def client_ip(request) -> str:
    """IP du client (fallback ``"unknown"`` si indisponible).

    Lit ``X-Forwarded-For`` (premier hop = client) uniquement quand
    ``DOMESTIQUE_AI_TRUSTED_PROXY`` est activé ; sinon retombe sur
    ``request.client.host`` pour ne pas se faire spoof un en-tête.
    """
    if get_trusted_proxy():
        headers = getattr(request, "headers", None)
        forwarded = headers.get("x-forwarded-for") if headers is not None else None
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host or "unknown"


def check(bucket: str, key: str, max_events: int, window_seconds: float) -> bool:
    """Enregistre un événement et dit s'il respecte la limite.

    Retourne ``True`` si l'événement est autorisé (moins de ``max_events``
    occurrences dans la fenêtre), ``False`` s'il doit être refusé (429). L'appel
    compte comme un événement même quand il est refusé (pas de contournement par
    rafale).
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    with _LOCK:
        events = _HITS[(bucket, key)]
        while events and events[0] < cutoff:
            events.popleft()
        allowed = len(events) < max_events
        events.append(now)
        return allowed


def reset() -> None:
    """Vide l'état (tests)."""
    with _LOCK:
        _HITS.clear()
