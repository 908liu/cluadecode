# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATA_DIR=/app/data \
    FRONTEND_DIST=/app/frontend/dist \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app/backend

# libgomp1 is required by onnxruntime (chromadb dependency).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

COPY --from=backend-builder --chown=10001:10001 /app/backend/.venv /app/backend/.venv
COPY --chown=10001:10001 backend/app ./app
COPY --chown=10001:10001 backend/tools ./tools
COPY --from=frontend --chown=10001:10001 /app/dist /app/frontend/dist

USER 10001:10001

EXPOSE 8000

CMD [".venv/bin/python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]