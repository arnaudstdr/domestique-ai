"""App FastAPI principale : monte les routers + sert le build React si présent."""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from domestique_ai.api.logging import get_logger, setup_logging
from domestique_ai.api.routers import (
    activities as activities_router,
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
    strava as strava_router,
)
from domestique_ai.config import REPO_ROOT

_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Logs au démarrage et à l'arrêt pour faciliter le diagnostic en prod."""
    setup_logging()
    log.info(
        "DomestiqueAI API démarrée — frontend_dist=%s (présent=%s)",
        _FRONTEND_DIST,
        _FRONTEND_DIST.is_dir(),
    )
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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log structuré pour chaque requête : id + durée + status.

    Le `request_id` est aussi exposé en header `X-Request-ID` côté réponse,
    pour qu'on puisse corréler un appel front avec sa trace serveur.
    """

    _LOG = get_logger("request")
    # Pas la peine de polluer les logs avec le polling /sync-status et les
    # tuiles de cartes statiques.
    _SKIP_PATHS = {"/api/strava/sync-status", "/api/health"}

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
        start = time.perf_counter()
        path = request.url.path
        method = request.method

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            self._LOG.exception(
                "rid=%s %s %s -> exception (%.1f ms)",
                request_id,
                method,
                path,
                duration_ms,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error.", "request_id": request_id},
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if path not in self._SKIP_PATHS:
            level_log = (
                self._LOG.warning if response.status_code >= 400 else self._LOG.info
            )
            level_log(
                "rid=%s %s %s -> %d (%.1f ms)",
                request_id,
                method,
                path,
                response.status_code,
                duration_ms,
            )
        response.headers["x-request-id"] = request_id
        return response


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
app.include_router(strava_router.router)
app.include_router(coach_router.router)


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
