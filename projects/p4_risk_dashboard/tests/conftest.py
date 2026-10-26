import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent / "p2_lakehouse"), str(HERE.parent.parent.parent)]

import pytest  # noqa: E402

from riskdash.agent import RiskInsightsAgent  # noqa: E402
from riskdash.engine import DuckDBEngine  # noqa: E402
from riskdash.metrics import MetricStore  # noqa: E402
from riskdash.synthetic import AS_OF, generate, load_p2_gold  # noqa: E402
from riskdash.tools import RiskTools  # noqa: E402

SPARK_AVAILABLE = shutil.which("java") is not None
try:
    import pyspark  # noqa: F401
except ImportError:
    SPARK_AVAILABLE = False


@pytest.fixture(scope="session")
def gold():
    return load_p2_gold()


@pytest.fixture(scope="session")
def mart(gold):
    return generate(gold)


@pytest.fixture(scope="session")
def store(mart, gold):
    """Read-only use only: the offline DuckDB metric store on the reporting date's data."""
    return MetricStore(DuckDBEngine(mart, gold))


@pytest.fixture
def tools(store):
    return RiskTools(store, AS_OF)


@pytest.fixture(scope="session")
def agent(store):
    return RiskInsightsAgent(store, AS_OF)


@pytest.fixture(scope="session")
def briefing(agent):
    return agent.run()


@pytest.fixture(scope="session")
def spark():
    if not SPARK_AVAILABLE:
        pytest.skip("PySpark/Java not available")
    from lakehouse.store import get_spark

    return get_spark("riskdash-tests")
