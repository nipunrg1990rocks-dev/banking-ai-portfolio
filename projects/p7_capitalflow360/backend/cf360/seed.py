"""CLI: create schema (via Alembic) and load the baseline. Usage: python -m cf360.seed [--no-process]"""

from __future__ import annotations

import argparse
import json
import time

from cf360.db import SessionLocal
from cf360.services import loader


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-process", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    with SessionLocal() as s:
        res = loader.seed(s, None if a.no_process else loader.STATEMENT_DATE)
    res["seconds"] = round(time.time() - t0, 1)
    res["rejected_count"] = len(res["rejected"])
    res["rejected"] = res["rejected"][:20]
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
