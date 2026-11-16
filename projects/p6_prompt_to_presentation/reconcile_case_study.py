"""Reconcile the workforce engine against the original case-study roster (run locally only).

The original roster file is NOT committed to this public repository. If you have it:

    python projects/p6_prompt_to_presentation/reconcile_case_study.py "path/to/DB_Case_Study_Test1_Excel.xlsx"

It loads the 'Data' sheet through the same validation gate the app uses and compares the
engine's figures with the numbers published in the case-study deck.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from deckpilot.domains.workforce import WorkforceDomain, load_original_roster  # noqa: E402
from deckpilot.spec import Comparison, DeckSpec, SlideRequest  # noqa: E402
from deckpilot.store import DatasetRegistry, QueryBuilder  # noqa: E402

EXPECTED = {  # figures published in the case-study deck / workbook
    "people on contract 30-Sep-2018": 134, "unique people on roster": 219, "FTE Jan-18": 195, "FTE Dec-18": 85,
    "contracts ending 31-Dec-2018": 105, "T&M on contract": 67, "external on contract": 87,
    "top-2 vendor external people": 54, "zero-allocation contracts": 20, "Q1 T&M India cost EUR": 610393.55,
    "Q1 T&M India contractors": 72, "run-rate Jan-18 EUR k": 691, "run-rate Dec-18 EUR k": 374,
}


def main(path: str) -> int:
    d = WorkforceDomain()
    reg = DatasetRegistry()
    ds = d.load(load_original_roster(path), Path(path).name, False, reg, snapshot="2018-09-30")
    qb = QueryBuilder(d.tables)

    def ctx(months, filters=None):
        spec = DeckSpec(domain="workforce", template="workforce_review", title="r", months=months,
                        filters=filters or {}, comparison=Comparison(mode="none"),
                        slides=[SlideRequest(slide_type="cover")] * 3)
        return d.context(spec, ds, qb)

    year = [f"2018-{m:02d}" for m in range(1, 13)]
    c = ctx(year)
    h = d._headline(c)
    mth = d.monthly(c)
    q1 = ctx(year[:3], {"country": ["IN"], "category": ["T&M"]})
    a = d._alloc(q1)
    a = a[a["reporting_month"].isin(year[:3])]
    got = {
        "people on contract 30-Sep-2018": h["on_contract"], "unique people on roster": h["unique"],
        "FTE Jan-18": round(h["fte0"]), "FTE Dec-18": round(h["fte1"]), "contracts ending 31-Dec-2018": h["cliff_n"],
        "T&M on contract": int((h["onc"]["category"] == "T&M").sum()), "external on contract": h["ext"],
        "top-2 vendor external people": h["top2"], "zero-allocation contracts": int(h["onc"]["zero_allocation"].sum()),
        "Q1 T&M India cost EUR": round(float(a["cost_eur"].sum()), 2),
        "Q1 T&M India contractors": int(a["person_key"].nunique()),
        "run-rate Jan-18 EUR k": round(mth.loc["2018-01", "cost"] / 1000),
        "run-rate Dec-18 EUR k": round(mth.loc["2018-12", "cost"] / 1000),
    }
    fails = 0
    print(f"{'Metric':40} {'Deck':>14} {'Engine':>14}  Match")
    for k, exp in EXPECTED.items():
        ok = abs(got[k] - exp) < 0.01
        fails += not ok
        print(f"{k:40} {exp:>14,} {got[k]:>14,}  {'yes' if ok else 'NO'}")
    print(f"\n{len(EXPECTED) - fails}/{len(EXPECTED)} figures reconcile.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
