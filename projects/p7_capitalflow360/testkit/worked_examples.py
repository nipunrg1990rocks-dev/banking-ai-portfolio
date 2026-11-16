"""Hand-calculated worked examples.

Each example states its inputs, the arithmetic done by hand (``working``) and the expected result.
``tests/test_worked_examples.py`` checks that BOTH the production engine (Decimal, cf360.rules.calc)
and the independent oracle (Fraction, testkit.oracle.reference) reproduce the hand result.
The same list is printed in the interview handbook.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from datagen.calendars import build_holidays, holiday_sets
from testkit.oracle import reference as ora

CCY_CAL = {"USD": "USNY", "EUR": "EUTA", "GBP": "GBLO", "JPY": "JPTO", "CAD": "CATO", "AUD": "AUSY", "CHF": "CHZU"}
_HOL = holiday_sets(build_holidays())


def _prod_cals():
    from cf360.rules.calc import Calendars
    return Calendars(_HOL)


def _ccy_hols():
    return {ccy: _HOL[code] for ccy, code in CCY_CAL.items()}


def _calc():
    from cf360.rules import calc
    return calc


CORP = {"coupon_frequency": 2, "issue_date": date(2020, 3, 15), "maturity_date": date(2030, 3, 15), "coupon_rate_pct": "6",
        "day_count": "30/360", "ex_dividend_bdays": 0}
UST = {"coupon_frequency": 2, "issue_date": date(2021, 2, 15), "maturity_date": date(2031, 2, 15), "coupon_rate_pct": "4",
       "day_count": "ACT/ACT", "ex_dividend_bdays": 0}
GILT = {"coupon_frequency": 2, "issue_date": date(2024, 12, 7), "maturity_date": date(2034, 12, 7), "coupon_rate_pct": "4",
        "day_count": "ACT/ACT", "ex_dividend_bdays": 7}


def X(id, title, rules, inputs, working, expected, prod, oracle):
    return {"id": id, "title": title, "rules": rules, "inputs": inputs, "working": working, "expected": expected,
            "prod": prod, "oracle": oracle}


EXAMPLES = [
    X("WE01", "Equity gross consideration", ["R020"], "BUY 1,000 shares at USD 25.50",
      "1,000 x 25.50 = 25,500.00", "25500.00",
      lambda: _calc().equity_economics("BUY", 1000, "25.50", "USD", 0, 0, 1, 0, 0)["gross"],
      lambda: ora.equity_charges("BUY", 1000, "25.50", 1, "USD", 0, 0, 0, 0)["gross"]),
    X("WE02", "Minimum commission applies", ["R021", "R023"], "BUY 100 at USD 10.00; 5 bps; minimum USD 25",
      "Gross 1,000.00. 5 bps = 0.50, below the USD 25 minimum, so commission = 25.00. Net = 1,000.00 + 25.00 = 1,025.00",
      "1025.00",
      lambda: _calc().equity_economics("BUY", 100, "10", "USD", 5, 25, 1, 0, 0)["net"],
      lambda: ora.equity_charges("BUY", 100, "10", 1, "USD", 5, 25, 0, 0)["net"]),
    X("WE03", "UK purchase with stamp duty, fee and minimum commission", ["R021", "R022", "R023"],
      "BUY 2,000 at GBP 4.25; 10 bps commission; minimum USD 25 at 1.25 USD/GBP; exchange fee 1 bp; stamp 50 bps",
      "Gross 8,500.00. Commission max(8.50, 25/1.25 = 20.00) = 20.00. Fee 0.85. Stamp 42.50. Net 8,500 + 20 + 0.85 + 42.50 = 8,563.35",
      "8563.35",
      lambda: _calc().equity_economics("BUY", 2000, "4.25", "GBP", 10, 25, "1.25", 1, 50)["net"],
      lambda: ora.equity_charges("BUY", 2000, "4.25", 1, "GBP", 10, 20, 1, 50)["net"]),
    X("WE04", "UK sale: no stamp duty, charges reduce proceeds", ["R022", "R023"], "SELL 2,000 at GBP 4.25, same charges",
      "Net 8,500.00 - 20.00 - 0.85 = 8,479.15 (stamp duty applies to purchases only)", "8479.15",
      lambda: _calc().equity_economics("SELL", 2000, "4.25", "GBP", 10, 25, "1.25", 1, 50)["net"],
      lambda: ora.equity_charges("SELL", 2000, "4.25", 1, "GBP", 10, 20, 1, 50)["net"]),
    X("WE05", "JPY has no minor unit", ["R021", "R028"], "BUY 300 at JPY 1,234.5; 8 bps; minimum USD 25 at 0.00625 USD/JPY",
      "Gross 370,350. 8 bps = 296.28 < 25/0.00625 = 4,000, so commission 4,000. Net 374,350 (whole yen)", "374350",
      lambda: _calc().equity_economics("BUY", 300, "1234.5", "JPY", 8, 25, "0.00625", 0, 0)["net"],
      lambda: ora.equity_charges("BUY", 300, "1234.5", 1, "JPY", 8, 4000, 0, 0)["net"]),
    X("WE06", "Average price from partial fills", ["R026"], "Fills 600 @ 10.00 and 400 @ 10.05",
      "(600 x 10.00 + 400 x 10.05) / 1,000 = (6,000 + 4,020) / 1,000 = 10.02", "10.020000",
      lambda: _calc().q_dp((D(600) * D("10.00") + D(400) * D("10.05")) / D(1000), 6),
      lambda: ora.round_dp(ora.vwap([(600, "10.00"), (400, "10.05")]), 6)),
    X("WE07", "Allocation residual goes to the largest allocation", ["R024", "R025"], "USD 1,000.00 over three equal allocations",
      "1,000 / 3 = 333.33 each, total 999.99. Residual 0.01 goes to the first of the tied largest: 333.34, 333.33, 333.33",
      "['333.34', '333.33', '333.33']",
      lambda: [str(x) for x in _calc().split_amount(D("1000.00"), [D(1), D(1), D(1)], "USD")],
      lambda: [str(x) for x in ora.allocate(D("1000.00"), [1, 1, 1], 3, "USD")]),
    X("WE08", "US corporate bond accrued interest, 30/360", ["R030", "R031"],
      "6% semi-annual, coupons 15 Mar/15 Sep, settle 20 May 2026, nominal 1,000,000",
      "30/360 days 15 Mar -> 20 May = 2 x 30 + 5 = 65. Accrued per 100 = 6 x 65 / 360 = 1.083333. Amount = 10,833.33",
      "10833.33",
      lambda: _calc().bond_economics(1000000, "99.50", _calc().accrued_per_100(CORP, date(2026, 5, 20)), "USD")["accrued"],
      lambda: ora.bond_consideration(1000000, "99.50", ora.accrued_interest_per_100(date(2026, 5, 20), 6, 2, "30/360",
                                                                                   date(2030, 3, 15), date(2020, 3, 15)), "USD")["accrued"]),
    X("WE09", "Bond gross consideration (clean + accrued)", ["R031"], "Same bond, clean 99.50",
      "Principal 1,000,000 x 99.50 / 100 = 995,000.00; plus accrued 10,833.33 = 1,005,833.33", "1005833.33",
      lambda: _calc().bond_economics(1000000, "99.50", _calc().accrued_per_100(CORP, date(2026, 5, 20)), "USD")["gross"],
      lambda: ora.bond_consideration(1000000, "99.50", ora.accrued_interest_per_100(date(2026, 5, 20), 6, 2, "30/360",
                                                                                   date(2030, 3, 15), date(2020, 3, 15)), "USD")["gross"]),
    X("WE10", "US Treasury accrued interest, ACT/ACT (ICMA)", ["R030"], "4% semi-annual, coupons 15 Feb/15 Aug, settle 1 May 2026, nominal 100,000",
      "Actual days 15 Feb -> 1 May = 75; period 15 Feb -> 15 Aug = 181. Accrued per 100 = 4/2 x 75/181 = 0.828729. Amount = 828.73",
      "828.73",
      lambda: _calc().bond_economics(100000, 100, _calc().accrued_per_100(UST, date(2026, 5, 1)), "USD")["accrued"],
      lambda: ora.bond_consideration(100000, 100, ora.accrued_interest_per_100(date(2026, 5, 1), 4, 2, "ACT/ACT",
                                                                               date(2031, 2, 15), date(2021, 2, 15)), "USD")["accrued"]),
    X("WE11", "Gilt in the ex-dividend period: negative accrued", ["R033"],
      "4% gilt, coupons 7 Jun/7 Dec, 7 business-day ex period, settle Mon 30 Nov 2026, nominal 100,000",
      "Ex-date = 7 London business days before 7 Dec = 26 Nov. Settlement is after it, so accrued = -(4/2) x 7/183 = -0.076503 per 100 = -76.50",
      "-76.50",
      lambda: _calc().bond_economics(100000, 100, _calc().accrued_per_100(GILT, date(2026, 11, 30), _prod_cals(), "GBP"), "GBP")["accrued"],
      lambda: ora.bond_consideration(100000, 100, ora.accrued_interest_per_100(
          date(2026, 11, 30), 4, 2, "ACT/ACT", date(2034, 12, 7), date(2024, 12, 7), 7, _HOL["GBLO"]), "GBP")["accrued"]),
    X("WE12", "FX spot skips a quote-currency holiday", ["R040"], "USD/JPY traded Tue 29 Dec 2026",
      "T+2 on both calendars: 30 Dec (open), 31 Dec (Tokyo closed), 1 Jan (both closed), 2-3 Jan weekend, 4 Jan (open) -> 4 Jan 2027",
      "2027-01-04",
      lambda: _calc().fx_value_date(_prod_cals(), date(2026, 12, 29), "USD", "JPY", None),
      lambda: ora.fx_spot_date(date(2026, 12, 29), "USD", "JPY", _ccy_hols())),
    X("WE13", "USD/CAD spot is T+1", ["R040"], "USD/CAD traded Fri 9 Oct 2026",
      "T+1: Mon 12 Oct is a holiday in New York (Columbus Day) and Toronto (Thanksgiving) -> Tue 13 Oct", "2026-10-13",
      lambda: _calc().fx_value_date(_prod_cals(), date(2026, 10, 9), "USD", "CAD", None),
      lambda: ora.fx_spot_date(date(2026, 10, 9), "USD", "CAD", _ccy_hols())),
    X("WE14", "FX counter amount in USD", ["R042"], "Buy EUR 1,000,000 at 1.08475",
      "1,000,000 x 1.08475 = 1,084,750.00 USD", "1084750.00",
      lambda: _calc().fx_counter(1000000, "1.08475", "USD"), lambda: ora.fx_counter_amount(1000000, "1.08475", "USD")),
    X("WE15", "FX forward outright with JPY pips", ["R043"], "USD/JPY spot 150.20, forward points -85.0",
      "JPY pip = 0.01, so 150.20 + (-85.0 / 100) = 149.35", "149.35",
      lambda: _calc().fx_outright("150.20", "-85.0", "JPY"), lambda: ora.round_dp(ora.fx_forward_rate("150.20", "-85.0", "JPY"), 2)),
    X("WE16", "Futures variation margin (long)", ["R051"], "Long 10 contracts, settle 4,500.25 -> 4,512.75, multiplier 50",
      "+1 x 10 x (4,512.75 - 4,500.25) x 50 = 10 x 12.50 x 50 = 6,250.00", "6250.00",
      lambda: _calc().futures_vm("BUY", 10, "4500.25", "4512.75", 50, "USD"),
      lambda: ora.futures_variation_margin("BUY", 10, "4500.25", "4512.75", 50, "USD")),
    X("WE17", "Option premium", ["R054"], "Buy 5 contracts at 3.45, multiplier 100",
      "5 x 3.45 x 100 = 1,725.00", "1725.00",
      lambda: _calc().option_premium(5, "3.45", 100, "USD"), lambda: ora.option_premium(5, "3.45", 100, "USD")),
    X("WE18", "Option not exercised below the 0.01 threshold", ["R053"], "Call strike 100.00, underlying 100.005",
      "Intrinsic 0.005 < 0.01, so no automatic exercise", "False",
      lambda: _calc().option_in_the_money("CALL", 100, "100.005")[0], lambda: ora.option_exercise("CALL", 100, "100.005")),
    X("WE19", "Option cash settlement on exercise", ["R053"], "5 calls, strike 100.00, underlying 101.50, multiplier 100",
      "(101.50 - 100.00) x 5 x 100 = 750.00", "750.00",
      lambda: _calc().q_ccy(_calc().option_in_the_money("CALL", 100, "101.50")[1] * 5 * 100, "USD"),
      lambda: ora.option_cash_settlement("CALL", 100, "101.50", 5, 100, "USD")),
    X("WE20", "Swap fixed leg, 30/360", ["R060"], "USD 10,000,000 at 3.50%, 16 Mar 2026 -> 16 Sep 2026",
      "30/360: 180/360 = 0.5. 10,000,000 x 3.5% x 0.5 = 175,000.00", "175000.00",
      lambda: _calc().swap_fixed(10000000, "3.5", date(2026, 3, 16), date(2026, 9, 16), "USD"),
      lambda: ora.swap_fixed_amount(10000000, "3.5", date(2026, 3, 16), date(2026, 9, 16), "USD")),
    X("WE21", "Swap floating leg, ACT/360 with spread", ["R061"], "USD 10,000,000, fixing 4.30% + 10 bp, 16 Mar -> 16 Jun 2026",
      "92 actual days. 10,000,000 x 4.40% x 92/360 = 112,444.444 -> 112,444.44", "112444.44",
      lambda: _calc().swap_float(10000000, "4.30", 10, date(2026, 3, 16), date(2026, 6, 16), "USD"),
      lambda: ora.swap_float_amount(10000000, "4.30", 10, date(2026, 3, 16), date(2026, 6, 16), "USD")),
    X("WE22", "Repo cash amount after haircut", ["R070"], "Nominal 10,000,000, dirty price 101.25, haircut 2%",
      "10,000,000 x 101.25/100 = 10,125,000; x (1 - 0.02) = 9,922,500.00", "9922500.00",
      lambda: _calc().repo_cash(10000000, "101.25", 2, "USD"), lambda: ora.repo_cash_amount(10000000, "101.25", 2, "USD")),
    X("WE23", "Repo interest, USD ACT/360", ["R071"], "Cash 9,922,500.00 at 5.30% for 7 days",
      "9,922,500 x 5.30% = 525,892.50 a year; x 7/360 = 10,225.6875 -> 10,225.69", "10225.69",
      lambda: _calc().repo_interest("9922500", "5.30", date(2026, 10, 1), date(2026, 10, 8), "USD"),
      lambda: ora.repo_interest("9922500", "5.30", date(2026, 10, 1), date(2026, 10, 8), "USD")),
    X("WE24", "Repo interest, GBP ACT/365", ["R071"], "Cash GBP 5,000,000 at 4.75% for 30 days",
      "5,000,000 x 4.75% = 237,500 a year; x 30/365 = 19,520.548 -> 19,520.55", "19520.55",
      lambda: _calc().repo_interest(5000000, "4.75", date(2026, 10, 1), date(2026, 10, 31), "GBP"),
      lambda: ora.repo_interest(5000000, "4.75", date(2026, 10, 1), date(2026, 10, 31), "GBP")),
    X("WE25", "Repo margin call above the minimum transfer amount", ["R072"],
      "Cash 9,800,000, haircut 2%, collateral now 9,900,000, MTA 50,000",
      "Required collateral 9,800,000 / 0.98 = 10,000,000. Deficit 100,000 > 50,000, so call 100,000.00", "100000.00",
      lambda: _calc().repo_margin_call(9800000, 9900000, 2, 50000, "USD"),
      lambda: ora.repo_margin_call(9800000, 9900000, 2, 50000, "USD")),
    X("WE26", "No margin call at exactly the MTA", ["R072"], "Same repo, collateral 9,950,000",
      "Deficit 10,000,000 - 9,950,000 = 50,000, not above the 50,000 MTA, so no call", "0.00",
      lambda: _calc().repo_margin_call(9800000, 9950000, 2, 50000, "USD"),
      lambda: ora.repo_margin_call(9800000, 9950000, 2, 50000, "USD")),
    X("WE27", "Securities-loan collateral, cross-currency", ["R073"], "Lend 10,000 shares worth 40.50 each in the collateral currency",
      "10,000 x 40.50 = 405,000; x 105% (cross-currency) = 425,250.00", "425250.00",
      lambda: _calc().secloan_collateral(10000, "40.50", True, "USD"),
      lambda: ora.secloan_collateral_required(10000, "40.50", False, "USD")),
    X("WE28", "Securities-loan fee", ["R073"], "10,000 shares at 45.00, 25 bps a year, 30 days",
      "10,000 x 45 = 450,000; x 0.25% = 1,125 a year; x 30/360 = 93.75", "93.75",
      lambda: _calc().secloan_fee(10000, 45, 25, date(2026, 10, 1), date(2026, 10, 31), "USD"),
      lambda: ora.secloan_fee(10000, 45, 25, date(2026, 10, 1), date(2026, 10, 31), "USD")),
]


def run_all() -> list[dict]:
    out = []
    for e in EXAMPLES:
        p, o = str(e["prod"]()), str(e["oracle"]())
        out.append({k: v for k, v in e.items() if k not in ("prod", "oracle")} | {"production": p, "oracle": o,
                                                                                  "pass": p == e["expected"] and o == e["expected"]})
    return out


if __name__ == "__main__":
    for r in run_all():
        print(f"{r['id']} {'PASS' if r['pass'] else 'FAIL'} expected={r['expected']} production={r['production']} oracle={r['oracle']}")
