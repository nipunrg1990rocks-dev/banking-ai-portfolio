import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent.parent)]

import pytest  # noqa: E402

from copilot.engine import ComplianceCopilot  # noqa: E402


@pytest.fixture(scope="session")
def shared_copilot():
    """Read-only use only: a copilot built once for retrieval-focused tests."""
    return ComplianceCopilot()


@pytest.fixture
def copilot():
    return ComplianceCopilot()
