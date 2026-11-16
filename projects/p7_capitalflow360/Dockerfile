# syntax=docker/dockerfile:1
# CapitalFlow 360: one image with the API and the built React UI.
# Build context is this folder:  docker compose up --build

FROM node:22.21.1-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
# Optional build secret "extra_ca": a CA bundle for builds behind a TLS-inspecting proxy. Not needed normally.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13.9-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend CF360_STATIC_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY data data
COPY reporting/out reporting/out
COPY --from=ui /ui/dist frontend/dist
RUN useradd --create-home --uid 10001 cf360 && chown -R cf360 /app
USER cf360
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["sh", "backend/docker-entrypoint.sh"]
