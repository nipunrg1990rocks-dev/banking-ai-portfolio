import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent.parent)]

SPARK_AVAILABLE = shutil.which("java") is not None
try:
    import delta  # noqa: F401
    import pyspark  # noqa: F401
except ImportError:
    SPARK_AVAILABLE = False

requires_spark = pytest.mark.skipif(not SPARK_AVAILABLE, reason="needs Java 17+ and requirements-pipeline.txt")


@pytest.fixture(scope="session")
def spark():
    if not SPARK_AVAILABLE:
        pytest.skip("PySpark/Delta/Java not available")
    from lakehouse.store import get_spark

    return get_spark("ledgerline-tests")


@pytest.fixture(scope="session")
def full_run(spark, tmp_path_factory):
    """One complete pipeline run (both batches) shared by the read-only tests."""
    from lakehouse.pipeline import Pipeline
    from lakehouse.store import LakehouseConfig, TableStore

    store = TableStore(spark, LakehouseConfig.local(tmp_path_factory.mktemp("lakehouse")))
    result = Pipeline(store).run_all()
    return store, result
