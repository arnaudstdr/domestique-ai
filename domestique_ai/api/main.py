"""App FastAPI principale : monte les routers + sert le build React si présent."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from domestique_ai.api.auth import BearerAuthMiddleware
from domestique_ai.api.logging import get_logger, setup_logging
from domestique_ai.api.routers import (
    activities as activities_router,
)
from domestique_ai.api.routers import (
    admin as admin_router,
)
from domestique_ai.api.routers import (
    announcement as announcement_router,
)
from domestique_ai.api.routers import (
    auth as auth_router,
)
from domestique_ai.api.routers import (
    availability as availability_router,
)
from domestique_ai.api.routers import (
    coach as coach_router,
)
from domestique_ai.api.routers import (
    feedback as feedback_router,
)
from domestique_ai.api.routers import (
    garmin as garmin_router,
)
from domestique_ai.api.routers import (
    google_health as google_health_router,
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
    prescriptions as prescriptions_router,
)
from domestique_ai.api.routers import (
    profile as profile_router,
)
from domestique_ai.api.routers import (
    roster as roster_router,
)
from domestique_ai.api.scheduler import start_scheduler, stop_scheduler
from domestique_ai.config import (
    REPO_ROOT,
    get_api_token,
    get_max_request_body_mb,
    get_sentry_dsn,
    get_sentry_enabled,
    get_sentry_send_pii,
)
from domestique_ai.llm.usage import llm_attribution
from domestique_ai.platform_db import init_platform_db

_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Logs au démarrage et à l'arrêt."""
    setup_logging()
    log.info(
        "DomestiqueAI API démarrée — frontend_dist=%s (présent=%s)",
        _FRONTEND_DIST,
        _FRONTEND_DIST.is_dir(),
    )
    init_platform_db()
    start_scheduler()
    yield
    stop_scheduler()
    log.info("DomestiqueAI API arrêtée.")


app = FastAPI(
    title="DomestiqueAI API",
    version="0.1.0",
    description=(
        "API d'accès aux activités cyclistes, à la charge d'entraînement, "
        "aux métriques matinales et au coach LLM."
    ),
    lifespan=lifespan,
    # La doc (Swagger/ReDoc) et le schéma OpenAPI sont volontairement absents :
    # le BearerAuthMiddleware ne filtre que ``/api/*``, donc ces routes
    # publiques contourneraient l'authentification (fuite de la surface d'API).
    # Le schéma reste générable hors HTTP via ``app.openapi()`` pour l'outillage.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


_SENTRY_REDACTED = "[Filtered]"


def _scrub_sentry_event(event: dict, hint: dict) -> dict:  # noqa: ARG001
    """Retire les secrets des événements Sentry avant envoi.

    Les tokens transitent parfois en query string (flux webcal ``?key=``) et le
    header ``Authorization`` porte le Bearer : on les remplace systématiquement,
    même quand ``SENTRY_SEND_PII`` est actif.
    """
    request = event.get("request")
    if not isinstance(request, dict):
        return event
    if request.get("query_string") is not None:
        request["query_string"] = _SENTRY_REDACTED
    url = request.get("url")
    if isinstance(url, str) and "?" in url:
        request["url"] = url.split("?", 1)[0]
    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in list(headers):
            if name.lower() == "authorization":
                headers[name] = _SENTRY_REDACTED
    return event


def _init_sentry() -> None:
    """Initialise Sentry si un DSN est configuré (best-effort, jamais bloquant).

    L'integrations FastAPI/Starlette sont activées automatiquement via le
    package ``sentry-sdk[fastapi]``. ``send_default_pii`` expose headers/IP —
    activé par défaut, désactivable via ``SENTRY_SEND_PII=0``. Dans tous les
    cas, ``before_send`` scrubbe la query string et le header ``Authorization``
    (tokens webcal/Bearer).
    """
    if not get_sentry_enabled():
        log.info("Sentry désactivé (SENTRY_ENABLED=0).")
        return
    dsn = get_sentry_dsn()
    if not dsn:
        log.info("Sentry désactivé (aucun SENTRY_DSN).")
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            send_default_pii=get_sentry_send_pii(),
            traces_sample_rate=1.0,
            before_send=_scrub_sentry_event,
        )
        log.info("Sentry initialisé (DSN configuré).")
    except Exception:  # noqa: BLE001 — ne doit jamais empêcher le démarrage
        log.exception("Échec de l'initialisation Sentry — poursuite sans supervision.")


_init_sentry()


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
    _SKIP_PATHS = {"/api/garmin/sync-status", "/api/health"}

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

        # Attribution des appels LLM lancés pendant la requête (table llm_calls) :
        # le user authentifié est posé dans le scope par BearerAuthMiddleware.
        # Posé ici (middleware ASGI, contexte renvoyé intact) et non dans
        # ``get_athlete_context`` (dépendance sync → threadpool, contextvar perdu).
        state = scope.get("state")
        user = state.get("user") if isinstance(state, dict) else None
        actor = user.get("public_id") if isinstance(user, dict) else None

        try:
            with llm_attribution(actor):
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
                level_log = self._LOG.warning if status_code >= 400 else self._LOG.info
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


class CacheControlMiddleware:
    """Politique de cache HTTP du build React servi à la racine.

    - assets hashés (``/assets/*``) → immuables 1 an : Vite change leur nom à
      chaque build, aucun risque de servir une version périmée.
    - app shell (``/``, ``/index.html``, ``/sw.js``, ``/manifest.webmanifest``)
      et toute réponse HTML → ``no-cache`` : le navigateur revalide à chaque
      fois (ETag/Last-Modified de Starlette). Cela évite un ``index.html`` figé
      — notamment sur iOS/PWA — sans casser le fallback offline, le service
      worker gardant sa propre copie en Cache Storage.

    L'API (``/api/*``) n'est jamais touchée (auth, SSE du coach, données perso).
    """

    _IMMUTABLE_PREFIX = "/assets/"
    _NO_CACHE_PATHS = {"/", "/index.html", "/sw.js", "/manifest.webmanifest"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @classmethod
    def _header_value(cls, path: str) -> str | None:
        if path.startswith(cls._IMMUTABLE_PREFIX):
            return "public, max-age=31536000, immutable"
        if path in cls._NO_CACHE_PATHS:
            return "no-cache"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        fixed = self._header_value(scope["path"])

        async def send_with_cache(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(key.lower() == b"cache-control" for key, _ in headers):
                    value = fixed
                    if value is None:
                        content_type = next(
                            (v for k, v in headers if k.lower() == b"content-type"),
                            b"",
                        )
                        if b"text/html" in content_type.lower():
                            value = "no-cache"
                    if value is not None:
                        headers.append((b"cache-control", value.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


class BodySizeLimitMiddleware:
    """Rejette en 413 les requêtes dont le corps dépasse la limite configurée.

    Le contrôle porte sur ``Content-Length`` : c'est le seul point où on peut
    refuser **avant** que Starlette ne bufferise le multipart (fichiers en
    mémoire puis ``SpooledTemporaryFile``). Un corps chunked sans
    ``Content-Length`` n'est pas borné ici — les handlers d'upload (avatar,
    import TCX) appliquent en plus leur propre plafond après lecture.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_length = _extract_header(scope, b"content-length")
        length: int | None = None
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError:
                length = None

        max_bytes = get_max_request_body_mb() * 1024 * 1024
        if length is not None and length > max_bytes:
            await self._send_413(send, max_bytes)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_413(send: Send, max_bytes: int) -> None:
        body = json.dumps(
            {"detail": f"Requête trop volumineuse ({max_bytes // (1024 * 1024)} Mo maximum)."}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# Ordre de la stack (de l'intérieur vers l'extérieur, donc inverse de
# l'ordre d'ajout) :
#   handler → BearerAuth → RequestLogging → CORS → CacheControl → BodySizeLimit
# Ainsi le RequestLogging trace aussi les 401 émis par BearerAuth, CORS répond
# aux preflights avant tout filtrage applicatif, et BodySizeLimit refuse les
# corps trop gros avant même l'authentification.
app.add_middleware(
    BearerAuthMiddleware,
    token=get_api_token(),
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

# Routeur d'identité : non gaté (gère lui-même /me, accept-invite public, etc.).
app.include_router(auth_router.router)

# Routeurs scopés par athlète (1b-i) : protégés par l'auth (chaque handler
# résout son AthleteContext via get_athlete_context) et isolés par espace de
# données. Plus de gate coach-only.
app.include_router(metrics_router.router)
app.include_router(activities_router.router)
app.include_router(morning_router.router)
app.include_router(google_health_router.router)
app.include_router(objective_router.router)
app.include_router(profile_router.router)
app.include_router(availability_router.router)
app.include_router(garmin_router.router)
app.include_router(coach_router.router)
app.include_router(plan_router.router)
app.include_router(roster_router.router)
app.include_router(prescriptions_router.router)

# Retours testeurs : data plateforme (platform.db), accessible à tout compte
# authentifié — non scopé par athlète.
app.include_router(feedback_router.router)

# Panneau d'administration plateforme : data cross-tenant (comptes, retours,
# réglages), réservé au rôle ``admin`` (non scopé par athlète).
app.include_router(admin_router.router)

# Annonce plateforme (bandeau) : lisible par tout compte authentifié.
app.include_router(announcement_router.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    """Endpoint de healthcheck (utilisé par Docker)."""
    return {"status": "ok"}


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` qui retombe sur ``index.html`` pour les routes inconnues.

    Sans ce fallback, naviguer en direct vers ``/login`` ou tout autre chemin
    géré par React Router renvoie un 404 ``{"detail":"Not Found"}`` parce que
    ``StaticFiles`` cherche un fichier physique correspondant. On laisse
    seulement passer le 404 si même ``index.html`` est manquant.
    """

    async def get_response(self, path, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Si le build React est disponible, le sert à la racine."""
    if not dist_dir.is_dir():
        return
    app.mount(
        "/",
        SPAStaticFiles(directory=str(dist_dir), html=True),
        name="frontend",
    )


_mount_frontend(app, _FRONTEND_DIST)
