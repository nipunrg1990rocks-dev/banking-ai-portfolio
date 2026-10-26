import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent.parent)]

import pytest  # noqa: E402

from opsagent.manager import ExceptionManager  # noqa: E402


@pytest.fixture
def manager():
    return ExceptionManager()


@pytest.fixture(scope="module")
def processed():
    """Read-only: a manager with the whole seed queue run once."""
    m = ExceptionManager()
    m.run_queue()
    return m


def find(m, predicate):
    """First seed exception id whose acceptance criteria match the predicate."""
    return next(e["exception_id"] for e in m.exceptions.values() if predicate(e))
