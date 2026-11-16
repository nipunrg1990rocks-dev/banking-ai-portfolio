"""Property-based tests (Hypothesis): production Decimal engine vs Fraction oracle on random inputs."""

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from cf360.rules import calc
from testkit.oracle import reference as ora
from testkit.worked_examples import _HOL, CCY_CAL

CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"]
qty = st.integers(min_value=1, max_value=5_000_000)
price = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("9999"), places=4)
CALS = calc.Calendars(_HOL)


@settings(max_examples=300, deadline=None)
@given(total=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1e10"), places=2),
       parts=st.lists(st.integers(min_value=1, max_value=10**7), min_size=1, max_size=12))
def test_allocation_amounts_always_sum_to_block(total, parts):
    out = calc.split_amount(total, [Decimal(p) for p in parts], "USD")
    assert sum(out) == total
    assert out == ora.allocate(total, parts, sum(parts), "USD")


@settings(max_examples=300, deadline=None)
@given(side=st.sampled_from(["BUY", "SELL"]), q=qty, p=price, ccy=st.sampled_from(CCYS),
       bps=st.decimals(min_value=0, max_value=50, places=2), stamp=st.sampled_from([0, 50]))
def test_equity_charges_match_oracle(side, q, p, ccy, bps, stamp):
    prod = calc.equity_economics(side, q, p, ccy, bps, 0, 1, 1, stamp)
    ref = ora.equity_charges(side, q, p, 1, ccy, bps, 0, 1, stamp)
    for k in ("gross", "commission", "exchange_fee", "tax", "net"):
        assert prod[k] == ref[k], k


@settings(max_examples=300, deadline=None)
@given(settle=st.dates(min_value=date(2024, 1, 2), max_value=date(2029, 12, 31)),
       dc=st.sampled_from(["30/360", "ACT/ACT", "ACT/365F", "ACT/360"]),
       cpn=st.decimals(min_value=Decimal("0.125"), max_value=Decimal("9"), places=3), freq=st.sampled_from([1, 2]))
def test_accrued_interest_matches_oracle(settle, dc, cpn, freq):
    bond = {"coupon_frequency": freq, "issue_date": date(2019, 6, 15), "maturity_date": date(2034, 6, 15),
            "coupon_rate_pct": str(cpn), "day_count": dc, "ex_dividend_bdays": 0}
    prod = calc.q_dp(calc.accrued_per_100(bond, settle), 10)
    ref = ora.round_dp(ora.accrued_interest_per_100(settle, cpn, freq, dc, date(2034, 6, 15), date(2019, 6, 15)), 10)
    assert prod == ref


@settings(max_examples=300, deadline=None)
@given(d=st.dates(min_value=date(2024, 1, 1), max_value=date(2035, 12, 31)), n=st.integers(min_value=0, max_value=10),
       cal=st.sampled_from(sorted(set(CCY_CAL.values()))))
def test_business_day_arithmetic_matches_oracle(d, n, cal):
    assert CALS.add(d, n, cal) == ora.add_business_days(d, n, [_HOL[cal]])


@settings(max_examples=200, deadline=None)
@given(td=st.dates(min_value=date(2024, 1, 2), max_value=date(2034, 12, 31)),
       pair=st.sampled_from([("EUR", "USD"), ("USD", "JPY"), ("GBP", "USD"), ("USD", "CAD"), ("AUD", "USD"), ("USD", "CHF"),
                             ("EUR", "GBP")]), months=st.sampled_from([0, 1, 3, 6, 12]))
def test_fx_value_dates_match_oracle(td, pair, months):
    hols = {c: _HOL[CCY_CAL[c]] for c in CCY_CAL}
    prod = calc.fx_value_date(CALS, td, pair[0], pair[1], months or None)
    ref = ora.fx_forward_date(td, *pair, months, hols) if months else ora.fx_spot_date(td, *pair, hols)
    assert prod == ref


@settings(max_examples=200, deadline=None)
@given(start=st.dates(min_value=date(2025, 1, 1), max_value=date(2030, 1, 1)), days=st.integers(min_value=1, max_value=400),
       ccy=st.sampled_from(CCYS), cash=st.decimals(min_value=1000, max_value=Decimal("1e9"), places=2),
       rate=st.decimals(min_value=Decimal("-0.5"), max_value=8, places=3))
def test_repo_interest_matches_oracle(start, days, ccy, cash, rate):
    end = start + timedelta(days=days)
    assert calc.repo_interest(cash, rate, start, end, ccy) == ora.repo_interest(cash, rate, start, end, ccy)
