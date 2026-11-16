"""Test configuration. Database tests use a separate database (CF360_TEST_DATABASE_URL, default cf360_test),
migrated with Alembic and loaded with the full baseline once per session."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TEST_DB = os.getenv("CF360_TEST_DATABASE_URL", "postgresql+psycopg://cf360:cf360@localhost:5432/cf360_test")
os.environ["CF360_DATABASE_URL"] = TEST_DB
os.environ["CF360_TEST_SUPPORT"] = "true"
os.environ.setdefault("CF360_JWT_SECRET", "test-only-secret-at-least-32-bytes-long!!")
os.environ.pop("CF360_DEFECTS", None)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PASSWORD = os.getenv("CF360_DEMO_PASSWORD", "Demo!2026")


@pytest.fixture(scope="session")
def seeded():
    """Migrate and seed the test database once (about 20 seconds)."""
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                   capture_output=True)
    from cf360.db import SessionLocal
    from cf360.services import loader, refcache
    with SessionLocal() as s:
        res = loader.seed(s)
    refcache.invalidate()
    return res


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient

    from cf360.main import app
    return TestClient(app)


@pytest.fixture(scope="session")
def auth(client):
    cache: dict[str, dict] = {}

    def get(user: str) -> dict:
        if user not in cache:
            r = client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
            assert r.status_code == 200, r.text
            cache[user] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return cache[user]
    return get


def pytest_collection_modifyitems(items):
    """Baseline invariants and the oracle comparison must see the untouched baseline, so the API tests
    (which book, amend and cancel trades) run last."""
    items.sort(key=lambda it: it.fspath.basename == "test_api.py")
