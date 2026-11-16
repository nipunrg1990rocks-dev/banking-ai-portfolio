import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent.parent)]

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from p6_helpers import APAC_PROMPT, DATA  # noqa: E402,F401

from deckpilot.domains.ops_risk import OpsRiskDomain  # noqa: E402
from deckpilot.domains.workforce import WorkforceDomain, synthetic_roster  # noqa: E402
from deckpilot.service import ReportingService  # noqa: E402
from deckpilot.store import DatasetRegistry, QueryBuilder  # noqa: E402


@pytest.fixture(scope="session")
def ops_raw() -> pd.DataFrame:
    return pd.read_csv(DATA / "ops_risk_synthetic.csv", dtype=str, keep_default_na=False, na_values=[""])


@pytest.fixture(scope="session")
def wf_raw() -> pd.DataFrame:
    return synthetic_roster()


@pytest.fixture(scope="session")
def ops_env(ops_raw):
    reg = DatasetRegistry()
    d = OpsRiskDomain()
    ds = d.load(ops_raw, "ops_risk_synthetic.csv", True, reg)
    reg.stage(ds)
    reg.activate(ds.version)
    return d, ds, QueryBuilder(d.tables)


@pytest.fixture(scope="session")
def wf_env(wf_raw):
    reg = DatasetRegistry()
    d = WorkforceDomain()
    ds = d.load(wf_raw, "roster.csv", True, reg, snapshot="2018-09-30")
    reg.stage(ds)
    reg.activate(ds.version)
    return d, ds, QueryBuilder(d.tables)


@pytest.fixture
def service(tmp_path) -> ReportingService:
    s = ReportingService(tmp_path, use_llm=False)
    s.bootstrap()
    return s

