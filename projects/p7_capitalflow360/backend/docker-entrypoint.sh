#!/bin/sh
# Migrate, load the synthetic baseline on first start (or redo a cut-off load), then serve the API and UI.
# A small placeholder answers on $PORT during the load, so hosts that check the port early see a live service.
cd /app/backend
python -m cf360.loading_server &
placeholder=$!
if python -m alembic upgrade head && python -m cf360.bootstrap; then
  echo "Start-up checks done"
else
  echo "START-UP FAILED: the database step above did not finish. Check CF360_DATABASE_URL (it must be a postgresql:// connection string)."
fi
kill "$placeholder" 2>/dev/null
wait "$placeholder" 2>/dev/null
exec uvicorn cf360.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
