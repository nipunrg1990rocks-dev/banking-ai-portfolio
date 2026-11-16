#!/bin/sh
# Migrate, load the synthetic baseline on first start only, then serve the API and UI.
set -e
cd /app/backend
python -m alembic upgrade head
if python -c "
from sqlalchemy import text
from cf360.db import engine
with engine.connect() as c:
    raise SystemExit(0 if c.scalar(text('select count(*) from trades')) == 0 else 1)
"; then
  echo "Empty database: loading the synthetic baseline (about a minute)"
  python -m cf360.seed
fi
exec uvicorn cf360.main:app --host 0.0.0.0 --port 8000 --proxy-headers
