"""App FastAPI principale : monte les routers + sert le build React si présent."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.logging import get_logger, setup_logging
from domestique_ai.api.routers import (
    activities as activities_router,
)
from domestique_ai.api.routers import (
    availability as availability_router,
)
from domestique_ai.api.routers import (
    coach as coach_router,
)
from domestique_ai.api.routers import (
    metrics as metrics_router,
)
from domestique_ai.api.routers import (
    morning as morning_router,
)
from domestique_ai.api.routers import (
    objective as objective_router,
)
from domestique_ai.api.routers import (
    plan as plan_router,
)
from domestique_ai.api.routers import (
    profile as profile_router,
)
from domestique_ai.api.routers import (
    strava as strava_router,
)
from domestique_ai.config import REPO_ROOT, get_api_token

_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

setup_logging()
log = get_logger("main")


async def _backfill_session_titles(limit: int = 30) -> None:
    """Génère les titres manquants pour les sessions existantes (best-effort).

    Tourne séquentiellement pour ne pas saturer Ollama, et s'arrête après 3
    échecs consécutifs (modèle indisponible). Idempotent : ne touche pas aux
    sessions qui ont déjà un titre.
    """
    from domestique_ai.llm.conversations import (
        generate_session_title,
        list_sessions,
    )

    sessions = list_sessions(limit=limit)
    missing = [s for s in sessions if not s.get("title") and s["messages"] >= 2]
    if not missing:
        return
    log.info("Backfill titres de session : %d candidates", len(missing))
    consecutive_failures = 0
    for sess in missing:
        try:
            title = await generate_session_title(sess["session_id"])
        except Exception:  # noqa: BLE001 — best-effort, jamais fatal
            log.exception(
                "Backfill titre échoué (%s)", sess["session_id"][:8]
            )
            title = None
        if title:
            consecutive_failures = 0
            log.info(
                "Backfill titre %s : %r", sess["session_id"][:8], title
            )
        else:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                log.warning(
                    "Backfill titres interrompu après 3 échecs consécutifs."
                )
                return


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Logs au démarrage et à l'arrêt + backfill titres en arrière-plan."""
    setup_logging()
    log.info(
        "DomestiqueAI API démarrée — frontend_dist=%s (présent=%s)",
        _FRONTEND_DIST,
        _FRONTEND_DIST.is_dir(),
    )
    # Lancement non bloquant — l'API est prête immédiatement, le backfill
    # tourne en tâche de fond.
    asyncio.create_task(_backfill_session_titles())
    yield
    log.info("DomestiqueAI API arrêtée.")


app = FastAPI(
    title="DomestiqueAI API",
    version="0.1.0",
    description=(
        "API d'accès aux activités cyclistes, à la charge d'entraînement, "
        "aux métriques matinales et au coach LLM."
    ),
    lifespan=lifespan,
)


def _cors_origins() -> list[str]:
    """Origines autorisées en dev (Vite). Override par DOMESTIQUE_AI_CORS_ORIGINS."""
    raw = os.getenv("DOMESTIQUE_AI_CORS_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


class RequestLoggingMiddleware:
    """Log structuré pour chaque requête : id + durée + status.

    Le `request_id` est aussi exposé en header `X-Request-ID` côté réponse,
    pour qu'on puisse corréler un appel front avec sa trace serveur.

    Implémentation en pure ASGI (pas `BaseHTTPMiddleware`) parce que ce
    dernier bufferise les réponses streamées via une `anyio.MemoryObjectStream`
    de buffer 0 qui casse `EventSourceResponse` (le SSE du coach restait bloqué
    sans jamais émettre vers le client).
    """

    _LOG = get_logger("request")
    # Pas la peine de polluer les logs avec le polling /sync-status et les
    # tuiles de cartes statiques.
    _SKIP_PATHS = {"/api/strava/sync-status", "/api/health"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_header(scope, b"x-request-id") or uuid.uuid4().hex[:8]
        start = time.perf_counter()
        path = scope["path"]
        method = scope["method"]
        status_code = 500

        async def send_with_logging(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_logging)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            self._LOG.exception(
                "rid=%s %s %s -> exception (%.1f ms)",
                request_id,
                method,
                path,
                duration_ms,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            if path not in self._SKIP_PATHS:
                level_log = (
                    self._LOG.warning if status_code >= 400 else self._LOG.info
                )
                level_log(
                    "rid=%s %s %s -> %d (%.1f ms)",
                    request_id,
                    method,
                    path,
                    status_code,
                    duration_ms,
                )


def _extract_header(scope: Scope, name: bytes) -> str | None:
    """Lit un header HTTP depuis le scope ASGI (insensible à la casse)."""
    lower = name.lower()
    for key, value in scope.get("headers", []):
        if key.lower() == lower:
            return value.decode("latin-1")
    return None


# Ordre de la stack (de l'intérieur vers l'extérieur, donc inverse de
# l'ordre d'ajout) :
#   handler → BearerAuth → RequestLogging → CORS
# Ainsi le RequestLogging trace aussi les 401 émis par BearerAuth, et CORS
# répond aux preflights avant tout filtrage applicatif.
app.add_middleware(BearerAuthMiddleware, token=get_api_token())
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

app.include_router(metrics_router.router)
app.include_router(activities_router.router)
app.include_router(morning_router.router)
app.include_router(objective_router.router)
app.include_router(profile_router.router)
app.include_router(availability_router.router)
app.include_router(strava_router.router)
app.include_router(coach_router.router)
app.include_router(plan_router.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    """Endpoint de healthcheck (utilisé par Docker)."""
    return {"status": "ok"}


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Si le build React est disponible, le sert à la racine."""
    if not dist_dir.is_dir():
        return
    app.mount(
        "/",
        StaticFiles(directory=str(dist_dir), html=True),
        name="frontend",
    )


_mount_frontend(app, _FRONTEND_DIST)
