"""App FastAPI principale : monte les routers + sert le build React si présent."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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

app = FastAPI(
    title="DomestiqueAI API",
    version="0.1.0",
    description=(
        "API d'accès aux activités cyclistes, à la charge d'entraînement, "
        "aux métriques matinales et au coach LLM."
    ),
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
