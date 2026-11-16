#!/bin/sh
# Migrate, load the synthetic baseline on first start (or redo a cut-off load), then serve the API and UI.
set -e
cd /app/backend
python -m alembic upgrade head
python -m cf360.bootstrap
exec uvicorn cf360.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
