"""Rate limiting in-process (fenêtre glissante) pour les endpoints publics sensibles.

Protège ``/api/auth/signup``, ``/api/auth/forgot-password`` et
``/api/auth/resend-verification`` contre l'abus (création massive de comptes,
spam SMTP, énumération).

Limitations assumées : l'état vit dans le process. En single-worker uvicorn
(cas de l'app) c'est suffisant ; en multi-worker, chaque worker a son propre
compteur. Derrière un reverse proxy, ``request.client.host`` peut être l'IP du
proxy — l'exploitation d'un en-tête ``X-Forwarded-For`` authentifié reste à
faire si l'app est exposée derrière un proxy.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def client_ip(request) -> str:
    """IP du client (fallback ``"unknown"`` si indisponible)."""
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
