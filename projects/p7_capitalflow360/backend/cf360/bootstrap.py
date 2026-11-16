"""Container start-up: load the synthetic baseline once, and redo it if an earlier start was cut off mid-load.

    python -m cf360.bootstrap      (run after `alembic upgrade head`; used by docker-entrypoint.sh)

The load commits in stages and ends with the reconciliation run, so a database with trades but no
reconciliation run is a half-finished load: it is wiped and loaded again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from cf360.db import engine


def state() -> tuple[int, int]:
    with engine.connect() as c:
        return c.scalar(text("select count(*) from trades")), c.scalar(text("select count(*) from recon_runs"))


def main() -> None:
    trades, recon_runs = state()
    if trades and recon_runs:
        print(f"Baseline already loaded ({trades} trades)")
        return
    if trades:
        print("Found a half-finished load: rebuilding the schema")
        with engine.begin() as c:
            c.execute(text("drop schema public cascade; create schema public"))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).resolve().parents[1], check=True)
    print("Loading the synthetic baseline (a few minutes on a small instance)", flush=True)
    subprocess.run([sys.executable, "-m", "cf360.seed"], check=True)


if __name__ == "__main__":
    main()
