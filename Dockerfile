# syntax=docker/dockerfile:1.7

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
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY domestique_ai ./domestique_ai

RUN pip install --upgrade pip \
 && pip install -e .

# Build React copié depuis le stage frontend
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8501/api/health || exit 1

CMD ["uvicorn", "domestique_ai.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8501"]
