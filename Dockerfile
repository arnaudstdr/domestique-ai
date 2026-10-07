# syntax=docker/dockerfile:1.27

# ---- Stage 1 : build du frontend React ----
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ .
RUN npm run build


# ---- Stage 2 : runtime Python ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv installé depuis l'image officielle (binaire statique, pas de pip).
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Dépendances d'abord (cache Docker) : résolution figée par uv.lock — plus de
# backtracking pip, build reproductible et rapide (notamment sur aarch64).
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY domestique_ai ./domestique_ai
RUN uv sync --frozen --no-dev

# Build React copié depuis le stage frontend
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8501/api/health || exit 1

CMD ["uvicorn", "domestique_ai.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8501", \
     "--no-server-header", \
     "--no-access-log"]
