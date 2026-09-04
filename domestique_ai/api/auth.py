"""Middleware Bearer pour l'API — CR-021.

L'app est mono-utilisateur mais exposée sur le tailnet et potentiellement
sur le LAN (cf. `docker-compose.yml` ``network_mode: host``). Sans auth
applicative, n'importe quel appareil du même réseau peut lire les
activités, déclencher un sync Garmin ou pousser un plan vers Garmin.

Comportement :
- Si ``DOMESTIQUE_AI_API_TOKEN`` est vide ou absent : middleware désactivé
  (mode dev local). Un warning est émis au boot.
- Sinon : toutes les requêtes ``/api/*`` doivent porter
  ``Authorization: Bearer <token>``. Les routes statiques (PWA bundle,
  ``/login``, assets) restent accessibles pour pouvoir afficher la mini
  page d'auth front. ``/api/health`` est explicitement exempté pour le
  healthcheck Docker.
- Comparaison du token en temps constant via ``hmac.compare_digest`` pour
  éviter les attaques par timing (paranoïaque vu le contexte, mais c'est
  une bonne pratique gratuite).
"""

from __future__ import annotations

import hmac
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

from domestique_ai.api.logging import get_logger
from domestique_ai.platform_db import (
    get_or_create_bootstrap_coach,
    resolve_session_token,
)


def _extract_header(scope: Scope, name: bytes) -> str | None:
    """Lit un header HTTP depuis le scope ASGI (insensible à la casse)."""
    lower = name.lower()
    for key, value in scope.get("headers", []):
        if key.lower() == lower:
            return value.decode("latin-1")
    return None


async def _send_401(send: Send) -> None:
    body = b'{"detail":"Unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"www-authenticate", b'Bearer realm="domestique-ai"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BearerAuthMiddleware:
    """Vérifie le header ``Authorization: Bearer <token>`` sur ``/api/*``.

    Routes exemptées :
    - ``/api/health`` (healthcheck Docker, sans token).
    - Tout ce qui ne commence pas par ``/api/`` (bundle PWA, ``/login``,
      assets, manifest, etc.) — la mini page d'auth front doit pouvoir se
      charger sans token.
    """

    _LOG = get_logger("auth")
    # Routes joignables sans session : accept-invite (entrée des comptes) et le
    # callback OAuth Google Health (redirection navigateur, validée par son `state`).
    _EXEMPT_API_PATHS = {
        "/api/health",
        "/api/auth/accept-invite",
        "/api/auth/reconnect",
        "/api/google-health/callback",
    }

    def __init__(
        self, app: ASGIApp, token: str | None, platform_db_path: Path | None = None
    ) -> None:
        self.app = app
        self._token = (token or "").strip()
        self._platform_db_path = platform_db_path
        self._enabled = bool(self._token)
        if not self._enabled:
            self._LOG.warning(
                "DOMESTIQUE_AI_API_TOKEN absent — auth Bearer désactivée. "
                "Acceptable en dev local, dangereux en prod."
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if not path.startswith("/api/") or path in self._EXEMPT_API_PATHS:
            await self.app(scope, receive, send)
            return

        # Laisser passer les preflights CORS — CORSMiddleware en amont y
        # répond, mais en cas de mauvaise configuration on évite de bloquer
        # le navigateur sur le 401.
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        header = _extract_header(scope, b"authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            await _send_401(send)
            return

        provided = header[len(prefix) :].strip()

        # Résolution : (a) token de session par utilisateur, sinon (b) token
        # legacy partagé → coach propriétaire (bootstrap). Sinon 401.
        user = resolve_session_token(provided, path=self._platform_db_path)
        if user is None and hmac.compare_digest(
            provided.encode("utf-8"), self._token.encode("utf-8")
        ):
            user = get_or_create_bootstrap_coach(path=self._platform_db_path)

        if user is None:
            await _send_401(send)
            return

        # Annoter le scope : current_user pour les dépendances en aval, et flag
        # d'authentification pour le logging.
        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["authenticated"] = True
            scope["state"]["user"] = user

        await self.app(scope, receive, send)


__all__ = ["BearerAuthMiddleware"]
