"""Regenerate the shipped synthetic datasets (deterministic seeds).

    python projects/p6_prompt_to_presentation/generate_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

import pandas as pd  # noqa: E402

from deckpilot.domains.ops_risk import refresh_variant, synthetic_ops_risk  # noqa: E402
from deckpilot.domains.workforce import synthetic_roster  # noqa: E402

DATA = HERE / "data"


def workforce_refresh(base: pd.DataFrame) -> pd.DataFrame:
    """October roster: 12 renewals backfilled into Nov-Dec, 10 zero-allocation records fixed, one bad row."""
    df = base.copy()
    months = [c for c in df.columns if c.endswith("-18")]
    pune_left = df[(df["City"] == "Pune") & (df["Category"] == "T&M") & (df["End Date"] == pd.Timestamp("2018-09-30"))]
    idx = pune_left.index[:12]
    df.loc[idx, "End Date"] = pd.Timestamp("2018-12-31")
    df.loc[idx, ["Oct-18", "Nov-18", "Dec-18"]] = [0.0, 1.0, 1.0]
    zero = df.index[(df[months].sum(axis=1) == 0) & (df["End Date"] == pd.Timestamp("2018-12-31"))][:10]
    df.loc[zero, months[6:]] = 1.0
    bad = df.iloc[[3]].copy()
    bad["Position #"] = "9999999999"
    bad["Name"] = "Test Record"
    bad["Mar-18"] = 1.5
    return pd.concat([df, bad], ignore_index=True)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    ops = synthetic_ops_risk()
    ops.to_csv(DATA / "ops_risk_synthetic.csv", index=False)
    refresh_variant(ops).to_csv(DATA / "ops_risk_refresh_oct2026.csv", index=False)
    wf = synthetic_roster()
    wf.to_csv(DATA / "workforce_roster_synthetic.csv", index=False, date_format="%Y-%m-%d")
    workforce_refresh(wf).to_csv(DATA / "workforce_roster_refresh_oct2018.csv", index=False, date_format="%Y-%m-%d")
    for p in sorted(DATA.glob("*.csv")):
        print(f"{p.name}: {p.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
