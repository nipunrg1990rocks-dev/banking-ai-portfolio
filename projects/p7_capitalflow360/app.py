"""Vercel entrypoint: the same FastAPI app the Docker image runs, served as a Python function.

Vercel (FastAPI preset) looks for a module-level `app` in app.py at the project root. The project root is this
folder (Vercel setting "Root Directory": projects/p7_capitalflow360). The build step (vercel.json) builds the React
UI into frontend/dist, which the app serves as it does in Docker. The database is the already-loaded Neon
PostgreSQL given in CF360_DATABASE_URL; this entrypoint never loads or changes the baseline.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "backend"))
if (HERE / "frontend" / "dist" / "index.html").exists():
    os.environ.setdefault("CF360_STATIC_DIR", str(HERE / "frontend" / "dist"))

from cf360.main import app  # noqa: E402

__all__ = ["app"]
