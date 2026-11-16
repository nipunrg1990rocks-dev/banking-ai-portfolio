"""Reference data for CapitalFlow 360: clients, accounts, counterparties, instruments, markets,
settlement conventions and SSIs. All names and identifiers are fictional."""

from __future__ import annotations

import hashlib
import random
from datetime import date

PRODUCT_GROUPS = ["EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"]
GROUP_LABEL = {"EQUITY": "Equities/ETFs", "BOND": "Corporate/government bonds", "FX": "FX spot/forwards",
               "LISTED_DERIV": "Futures/options", "IRS": "Interest-rate swaps", "FINANCING": "Repo/securities lending"}
PRODUCT_TYPES = {
    "EQUITY": ["EQUITY", "ETF"], "BOND": ["GOVT_BOND", "CORP_BOND"], "FX": ["FX_SPOT", "FX_FORWARD"],
    "LISTED_DERIV": ["FUTURE", "OPTION"], "IRS": ["IRS"],
    "FINANCING": ["REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"],
}
BOOKS = {"EQUITY": ["EQ-CASH-NY", "EQ-CASH-LDN", "EQ-CASH-TKY"], "BOND": ["RATES-GOVT", "CREDIT-CORP"],
         "FX": ["FX-SPOT-FWD"], "LISTED_DERIV": ["DERIV-LISTED"], "IRS": ["RATES-SWAPS"],
         "FINANCING": ["FIN-REPO-SL"]}

MARKETS = [
    # market, calendar, timezone, currency, settlement_location, exchange_fee_bps, stamp_duty_bps
    ("XNYS", "XNYS", "America/New_York", "USD", "SIM-US-CSD", "0.20", "0"),
    ("XLON", "XLON", "Europe/London", "GBP", "SIM-UK-CSD", "0.10", "50"),
    ("XETR", "XETR", "Europe/Berlin", "EUR", "SIM-EU-CSD", "0.15", "0"),
    ("XTKS", "XTKS", "Asia/Tokyo", "JPY", "SIM-JP-CSD", "0.10", "0"),
    ("XCME", "XCME", "America/Chicago", "USD", "SIM-CCP-US", "0", "0"),
    ("XCBO", "XCBO", "America/Chicago", "USD", "SIM-CCP-US", "0", "0"),
    ("XEUR", "XEUR", "Europe/Berlin", "EUR", "SIM-CCP-EU", "0", "0"),
    ("OTC-US", "USNY", "America/New_York", "USD", "SIM-US-CSD", "0", "0"),
    ("OTC-UK", "GBLO", "Europe/London", "GBP", "SIM-UK-CSD", "0", "0"),
    ("OTC-EU", "EUTA", "Europe/Berlin", "EUR", "SIM-EU-CSD", "0", "0"),
    ("OTC-JP", "JPTO", "Asia/Tokyo", "JPY", "SIM-JP-CSD", "0", "0"),
    ("OTC-FX", "USNY", "Europe/London", "USD", "SIM-PAY", "0", "0"),
    ("OTC-RATES", "GBLO", "Europe/London", "USD", "SIM-CCP-RATES", "0", "0"),
]
MARKET = {m[0]: m for m in MARKETS}

# Effective-dated settlement conventions. offset is in business days on the calendar basis.
CONVENTIONS = [
    # id, product_type, market, offset, calendar_basis, cutoff_local, tz, eff_from, eff_to, source, note
    ("SC-EQ-US-T2", "EQUITY|ETF", "XNYS", 2, "MARKET+CCY", "15:00", "America/New_York", date(2017, 9, 5),
     date(2024, 5, 27), "SEC Rule 15c6-1 (T+2, 2017)", "Historical row kept for temporal tests"),
    ("SC-EQ-US-T1", "EQUITY|ETF", "XNYS", 1, "MARKET+CCY", "15:00", "America/New_York", date(2024, 5, 28),
     None, "SEC Rule 15c6-1 amendments, compliance date 28 May 2024", ""),
    ("SC-EQ-UK", "EQUITY|ETF", "XLON", 2, "MARKET+CCY", "16:00", "Europe/London", date(2014, 10, 6), None,
     "UK standard settlement T+2 (UK T+1 planned from 11 Oct 2027)", ""),
    ("SC-EQ-DE", "EQUITY|ETF", "XETR", 2, "MARKET+CCY", "16:00", "Europe/Berlin", date(2014, 10, 6), None,
     "EU CSDR Art. 5(2) T+2 (EU T+1 planned from 11 Oct 2027)", ""),
    ("SC-EQ-JP", "EQUITY|ETF", "XTKS", 2, "MARKET+CCY", "15:00", "Asia/Tokyo", date(2019, 7, 16), None,
     "JPX equity settlement T+2 since 16 Jul 2019", ""),
    ("SC-GB-US", "GOVT_BOND|CORP_BOND", "OTC-US", 1, "CCY", "15:00", "America/New_York", date(2024, 5, 28), None,
     "US Treasury and US corporate market practice T+1", "Corporate T+1 applies from 28 May 2024"),
    ("SC-GB-UK", "GOVT_BOND|CORP_BOND", "OTC-UK", 1, "CCY", "16:00", "Europe/London", date(2014, 1, 1), None,
     "Gilt market convention T+1", "Simplification: UK corporate bonds also T+1"),
    ("SC-GB-EU", "GOVT_BOND|CORP_BOND", "OTC-EU", 2, "CCY", "16:00", "Europe/Berlin", date(2014, 10, 6), None,
     "EU CSDR T+2 for transferable securities on trading venues", "Applied to all EUR bonds"),
    ("SC-GB-JP", "GOVT_BOND|CORP_BOND", "OTC-JP", 1, "CCY", "14:00", "Asia/Tokyo", date(2018, 5, 1), None,
     "JGB outright settlement T+1 since May 2018", ""),
    ("SC-OPT-US", "OPTION", "XCBO", 1, "MARKET+CCY", "15:00", "America/Chicago", date(2024, 5, 28), None,
     "US listed option premium settles T+1", ""),
    ("SC-OPT-EU", "OPTION", "XEUR", 1, "MARKET+CCY", "16:00", "Europe/Berlin", date(2014, 1, 1), None,
     "Exchange rule premium T+1", "Simplified"),
    ("SC-FUT", "FUTURE", "*", 0, "MARKET", "17:00", "America/Chicago", date(2014, 1, 1), None,
     "Futures: daily variation margin, no notional delivery", "Simplification: VM settles same business day"),
    ("SC-IRS", "IRS", "*", 2, "CCY", "16:00", "Europe/London", date(2014, 1, 1), None,
     "Spot-starting IRS effective date T+2", "Simplification: T+2 for every currency"),
    ("SC-REPO", "REPO|REVERSE_REPO", "*", 0, "CCY", "15:00", "Europe/London", date(2014, 1, 1), None,
     "Repo start date as agreed (validated as a business day)", "Offset 0 means 'as agreed'"),
    ("SC-SL-US", "SEC_LOAN|SEC_BORROW", "XNYS", 1, "MARKET+CCY", "15:00", "America/New_York", date(2024, 5, 28), None,
     "Securities lending settles on the underlying's cycle", ""),
    ("SC-SL-OTHER", "SEC_LOAN|SEC_BORROW", "*", 2, "MARKET+CCY", "16:00", "Europe/London", date(2014, 1, 1), None,
     "Securities lending settles on the underlying's cycle", ""),
    ("SC-FX", "FX_SPOT|FX_FORWARD", "OTC-FX", 2, "BOTH_CCY", "17:00", "America/New_York", date(2014, 1, 1), None,
     "FX spot T+2 on both currency calendars; USD/CAD T+1", "No USD-holiday special case for non-USD pairs"),
]

CLIENT_NAMES = [
    ("Harbourline Pension Fund", "PENSION_FUND", "GB"), ("Northwind Asset Management", "ASSET_MANAGER", "US"),
    ("Silverbirch Capital Partners", "HEDGE_FUND", "US"), ("Meridian Mutual Insurance", "INSURER", "DE"),
    ("Kestrel Global Investors", "ASSET_MANAGER", "GB"), ("Aoi Trust Asset Management", "ASSET_MANAGER", "JP"),
    ("Granite Peak Endowment", "PENSION_FUND", "US"), ("Riverstone Treasury Services", "CORPORATE", "GB"),
    ("Bluefjord Macro Fund", "HEDGE_FUND", "CH"), ("Ledgerline Treasury (internal)", "BANK", "GB"),
    ("Cobalt Ridge Advisors", "ASSET_MANAGER", "US"), ("Emberfield Family Office", "PRIVATE_CLIENT", "GB"),
    ("Tidewater Municipal Fund", "PENSION_FUND", "US"), ("Larchmont Re Insurance", "INSURER", "CH"),
    ("Sakura Bay Life", "INSURER", "JP"), ("Maple Crest Pension Plan", "PENSION_FUND", "CA"),
    ("Southern Cross Super", "PENSION_FUND", "AU"), ("Orion Credit Opportunities", "HEDGE_FUND", "US"),
    ("Vantage Point Wealth", "PRIVATE_CLIENT", "US"), ("Rhine Valley Bank", "BANK", "DE"),
    ("Quayside Corporate Treasury", "CORPORATE", "GB"), ("Pinecrest Retail Investor", "PRIVATE_CLIENT", "GB"),
    ("Arcadia Sovereign Reserve", "BANK", "AU"), ("Halcyon Multi-Strategy", "HEDGE_FUND", "GB"),
]

COUNTERPARTIES = [
    ("CP01", "Northgate Securities", "BROKER", "US", "Y"), ("CP02", "Thames Street Markets", "DEALER", "GB", "Y"),
    ("CP03", "Alpenhorn Bank", "BANK", "CH", "Y"), ("CP04", "Kanto Capital Markets", "BROKER", "JP", "N"),
    ("CP05", "Frankfurter Handelshaus", "DEALER", "DE", "Y"), ("CP06", "Bayview Execution Services", "BROKER", "US", "N"),
    ("CP07", "Maplewood Dealer Bank", "BANK", "CA", "Y"), ("CP08", "Coral Sea Securities", "BROKER", "AU", "N"),
    ("CP09", "Westminster Prime Finance", "DEALER", "GB", "Y"), ("CP10", "Hudson Rates Desk", "BANK", "US", "Y"),
    ("CCP-US", "Simulated US Clearing House", "CCP", "US", "Y"), ("CCP-EU", "Simulated EU Clearing House", "CCP", "DE", "Y"),
    ("CCP-RATES", "Simulated Rates Clearing House", "CCP", "GB", "Y"),
    ("CU01", "Ledgerline Custody", "CUSTODIAN", "GB", "N"), ("CU02", "Bluewater Custody", "CUSTODIAN", "US", "N"),
    ("CU03", "Harbour Trust Custody", "CUSTODIAN", "JP", "N"),
]


def synthetic_lei(rng: random.Random, prefix: str) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
    return (prefix + "".join(rng.choice(alphabet) for _ in range(20)))[:20]


def build_clients(rng: random.Random) -> list[dict]:
    rows = []
    for i, (name, ctype, country) in enumerate(CLIENT_NAMES, start=1):
        cid = f"CL{i:03d}"
        category = "ELIGIBLE_COUNTERPARTY" if ctype in ("BANK", "INSURER") else "PROFESSIONAL"
        if ctype == "PRIVATE_CLIENT" and i in (12, 22):
            category = "RETAIL"
        status = "ACTIVE"
        kyc_expiry = date(2027, rng.randint(1, 12), 15)
        if cid == "CL022":
            status = "SUSPENDED"
        if cid == "CL019":
            kyc_expiry = date(2026, 10, 31)  # expires inside the period: used by temporal negative cases
        base = {"US": "USD", "GB": "GBP", "DE": "EUR", "JP": "JPY", "CH": "CHF", "CA": "CAD", "AU": "AUD"}[country]
        rows.append({
            "client_id": cid, "legal_name": name, "short_name": name.split()[0].upper()[:10],
            "lei": synthetic_lei(rng, "SYN0"), "client_type": ctype, "client_category": category,
            "country": country, "base_ccy": base, "status": status,
            "kyc_review_date": date(2026, rng.randint(1, 6), 1), "kyc_expiry_date": kyc_expiry,
            "risk_rating": rng.choice(["LOW", "MEDIUM", "MEDIUM", "HIGH"]),
            "exposure_limit_usd": rng.choice([250, 400, 600, 900, 1500]) * 1_000_000,
            "netting_agreement": "Y" if ctype in ("BANK", "HEDGE_FUND", "ASSET_MANAGER") else "N",
            "source_system": "CRM-SIM", "source_record_id": f"CRM-{1000 + i}",
        })
    return rows


def client_products(c: dict) -> list[str]:
    t = c["client_type"]
    if c["client_category"] == "RETAIL":
        return ["EQUITY", "BOND", "FX"]
    return {
        "PENSION_FUND": ["EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"],
        "ASSET_MANAGER": ["EQUITY", "BOND", "FX", "LISTED_DERIV", "FINANCING"],
        "HEDGE_FUND": ["EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"],
        "INSURER": ["EQUITY", "BOND", "FX", "IRS", "FINANCING"],
        "CORPORATE": ["FX", "IRS", "BOND"],
        "BANK": ["BOND", "FX", "IRS", "FINANCING", "LISTED_DERIV"],
        "PRIVATE_CLIENT": ["EQUITY", "BOND", "FX", "LISTED_DERIV"],
    }[t]


def build_accounts(rng: random.Random, clients: list[dict]) -> tuple[list[dict], list[dict]]:
    accounts, schedules = [], []
    n = 0
    for c in clients:
        prods = client_products(c)
        n_acc = 4 if c["client_type"] in ("ASSET_MANAGER", "PENSION_FUND") else (3 if c["client_type"] == "HEDGE_FUND" else 2)
        for k in range(n_acc):
            n += 1
            aid = f"AC{n:04d}"
            custodian = {"JP": "CU03", "US": "CU02"}.get(c["country"], "CU01")
            accounts.append({
                "account_id": aid, "client_id": c["client_id"],
                "account_name": f"{c['short_name']} {'Block' if k == 0 else 'Fund ' + chr(64 + k)}",
                "account_type": "BLOCK" if k == 0 else "FUND", "base_ccy": c["base_ccy"],
                "permitted_products": ",".join(prods), "custodian_id": custodian,
                "securities_account_ref": f"SA-{aid}", "cash_account_ref": f"CA-{aid}",
                "partial_settlement_allowed": "N" if k == 1 else "Y",
                "credit_line_usd": rng.choice([0, 0, 5_000_000, 25_000_000]),
                "status": "ACTIVE", "opened_date": date(2020 + rng.randint(0, 5), rng.randint(1, 12), 1),
                "source_system": "ACCT-SIM", "source_record_id": f"ACCT-{5000 + n}",
            })
            for g in prods:
                bps = {"EQUITY": rng.choice(["5", "7.5", "10", "12"]), "LISTED_DERIV": "0", "BOND": "0", "FX": "0",
                       "IRS": "0", "FINANCING": "0"}[g]
                min_usd = {"EQUITY": rng.choice(["15", "25", "40"])}.get(g, "0")
                if g == "EQUITY" and n % 3 == 0:
                    # commission schedule repriced from 1 Oct 2026 (effective-dated join)
                    schedules.append({"schedule_id": f"CS-{aid}-{g}-1", "account_id": aid, "product_group": g,
                                      "commission_bps": bps, "min_commission_usd": min_usd,
                                      "effective_from": date(2024, 1, 1), "effective_to": date(2026, 9, 30)})
                    schedules.append({"schedule_id": f"CS-{aid}-{g}-2", "account_id": aid, "product_group": g,
                                      "commission_bps": str(float(bps) - 2.5).rstrip("0").rstrip("."),
                                      "min_commission_usd": min_usd,
                                      "effective_from": date(2026, 10, 1), "effective_to": None})
                else:
                    schedules.append({"schedule_id": f"CS-{aid}-{g}-1", "account_id": aid, "product_group": g,
                                      "commission_bps": bps, "min_commission_usd": min_usd,
                                      "effective_from": date(2024, 1, 1), "effective_to": None})
    return accounts, schedules


def build_counterparties(rng: random.Random) -> list[dict]:
    rows = []
    for cid, name, ctype, country, netting in COUNTERPARTIES:
        rows.append({"counterparty_id": cid, "legal_name": name, "counterparty_type": ctype, "country": country,
                     "lei": synthetic_lei(rng, "SYN1"), "netting_agreement": netting, "status": "ACTIVE",
                     "source_system": "CPTY-SIM", "source_record_id": f"CPTY-{cid}"})
    return rows


EQ_NAMES = {
    "XNYS": ["Nova Robotics", "Kelpstone Foods", "Brightwater Utilities", "Quantum Freight", "Cedar Health",
             "Ironclad Insurance", "Lumen Semiconductors", "Harvest Grain", "Polaris Aero", "Mosaic Retail",
             "Summit Telecom", "Bluegate Software"],
    "XLON": ["Albion Mining", "Thamesbridge Bank", "Wessex Water", "Pennine Pharma", "Kingsway Retail",
             "Solent Shipping", "Cotswold Brewing"],
    "XETR": ["Rheinwerk Motoren", "Elbtal Chemie", "Bavaria Logistik", "Spree Energie", "Hanse Versicherung"],
    "XTKS": ["Fuji Precision", "Kansai Rail", "Hokuto Electronics", "Sumida Trading", "Akita Foods"],
}
ETF_NAMES = {"XNYS": ["CF US Broad Market ETF", "CF Short Treasury ETF", "CF Tech Leaders ETF"],
             "XLON": ["CF UK 100 Tracker ETF", "CF Global Bond ETF GBP"], "XETR": ["CF Euro Stoxx Tracker ETF"],
             "XTKS": ["CF Japan TOPIX Tracker ETF"]}


def build_instruments(rng: random.Random) -> dict[str, list[dict]]:
    common, equities, bonds, fxp, derivs, swaps = [], [], [], [], [], []
    n = 0

    def add_common(iid, ptype, group, name, market, ccy, status="ACTIVE", maturity=None):
        common.append({"instrument_id": iid, "product_type": ptype, "product_group": group, "name": name,
                       "synthetic_isin": "SY" + hashlib.sha1(iid.encode()).hexdigest()[:10].upper(),
                       "market": market, "currency": ccy, "status": status, "maturity_date": maturity,
                       "source_system": "REF-SIM", "source_record_id": f"REF-{iid}"})

    px = {}
    for market, names in EQ_NAMES.items():
        ccy = MARKET[market][3]
        for nm in names:
            n += 1
            iid = f"EQ{n:03d}"
            ticker = "".join(w[0] for w in nm.split())[:3].upper() + str(n)
            add_common(iid, "EQUITY", "EQUITY", nm, market, ccy)
            lot = 100 if market == "XTKS" else 1
            equities.append({"instrument_id": iid, "ticker": ticker, "lot_size": lot, "sector": rng.choice(
                ["Industrials", "Financials", "Technology", "Utilities", "Consumer", "Healthcare", "Energy"]),
                "stamp_duty_exempt": "N", "dividend_frequency": rng.choice([1, 2, 4])})
            px[iid] = {"XTKS": rng.randint(800, 9000), "XLON": round(rng.uniform(2, 40), 2)}.get(
                market, round(rng.uniform(20, 400), 2))
    for market, names in ETF_NAMES.items():
        ccy = MARKET[market][3]
        for nm in names:
            n += 1
            iid = f"EF{n:03d}"
            add_common(iid, "ETF", "EQUITY", nm, market, ccy)
            equities.append({"instrument_id": iid, "ticker": "CF" + str(n), "lot_size": 100 if market == "XTKS" else 1,
                             "sector": "Fund", "stamp_duty_exempt": "Y", "dividend_frequency": 4})
            px[iid] = {"XTKS": rng.randint(1500, 3000), "XLON": round(rng.uniform(5, 80), 2)}.get(
                market, round(rng.uniform(30, 500), 2))

    bond_specs = [
        # issuer, type, market, ccy, coupon, freq, daycount, issue, maturity, min_denom, incr, ex_div
        ("US Treasury (synthetic)", "GOVT_BOND", "OTC-US", "USD", "4.250", 2, "ACT/ACT", date(2024, 11, 15), date(2029, 11, 15), 100, 100, 0),
        ("US Treasury (synthetic)", "GOVT_BOND", "OTC-US", "USD", "3.875", 2, "ACT/ACT", date(2023, 8, 15), date(2033, 8, 15), 100, 100, 0),
        ("US Treasury (synthetic)", "GOVT_BOND", "OTC-US", "USD", "4.625", 2, "ACT/ACT", date(2025, 2, 28), date(2030, 2, 28), 100, 100, 0),
        ("UK Gilt (synthetic)", "GOVT_BOND", "OTC-UK", "GBP", "4.125", 2, "ACT/ACT", date(2023, 1, 22), date(2029, 1, 22), 1, 1, 7),
        ("UK Gilt (synthetic)", "GOVT_BOND", "OTC-UK", "GBP", "3.750", 2, "ACT/ACT", date(2022, 10, 7), date(2032, 10, 7), 1, 1, 7),
        ("German Bund (synthetic)", "GOVT_BOND", "OTC-EU", "EUR", "2.500", 1, "ACT/ACT", date(2024, 2, 15), date(2034, 2, 15), 1000, 1000, 0),
        ("German Bund (synthetic)", "GOVT_BOND", "OTC-EU", "EUR", "2.200", 1, "ACT/ACT", date(2025, 4, 15), date(2030, 4, 15), 1000, 1000, 0),
        ("Japan Govt Bond (synthetic)", "GOVT_BOND", "OTC-JP", "JPY", "0.900", 2, "ACT/365F", date(2024, 9, 20), date(2034, 9, 20), 50000, 50000, 0),
        ("Polaris Aero Corp", "CORP_BOND", "OTC-US", "USD", "5.375", 2, "30/360", date(2023, 10, 31), date(2030, 10, 31), 1000, 1000, 0),
        ("Cedar Health Corp", "CORP_BOND", "OTC-US", "USD", "4.950", 2, "30/360", date(2024, 3, 1), date(2031, 3, 1), 1000, 1000, 0),
        ("Summit Telecom Corp", "CORP_BOND", "OTC-US", "USD", "6.100", 2, "30/360", date(2022, 12, 15), date(2032, 12, 15), 1000, 1000, 0),
        ("Lumen Semiconductors Corp", "CORP_BOND", "OTC-US", "USD", "5.000", 2, "30/360", date(2025, 7, 31), date(2028, 7, 31), 1000, 1000, 0),
        ("Rheinwerk Motoren AG", "CORP_BOND", "OTC-EU", "EUR", "3.625", 1, "ACT/ACT", date(2024, 9, 25), date(2031, 9, 25), 100000, 1000, 0),
        ("Hanse Versicherung AG", "CORP_BOND", "OTC-EU", "EUR", "4.000", 1, "ACT/ACT", date(2023, 11, 6), date(2033, 11, 6), 100000, 1000, 0),
        ("Thamesbridge Bank plc", "CORP_BOND", "OTC-EU", "EUR", "3.250", 1, "ACT/ACT", date(2025, 10, 1), date(2029, 10, 1), 100000, 1000, 0),
    ]
    for issuer, ptype, market, ccy, cpn, freq, dc, issue, mat, mind, incr, exdiv in bond_specs:
        n += 1
        iid = f"BD{n:03d}"
        name = f"{issuer} {cpn}% {mat.strftime('%b %Y')}"
        add_common(iid, ptype, "BOND", name, market, ccy, maturity=mat)
        bonds.append({"instrument_id": iid, "issuer": issuer, "coupon_rate_pct": cpn, "coupon_frequency": freq,
                      "day_count": dc, "issue_date": issue, "maturity_date": mat, "min_denomination": mind,
                      "min_increment": incr, "ex_dividend_bdays": exdiv,
                      "credit_rating": "AAA" if ptype == "GOVT_BOND" else rng.choice(["A", "BBB", "BBB", "A"])})
        px[iid] = round(rng.uniform(94, 104), 3)

    for pair in ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "USDCHF", "EURGBP", "EURJPY"]:
        n += 1
        iid = f"FX{pair}"
        add_common(iid, "FX_PAIR", "FX", f"{pair[:3]}/{pair[3:]}", "OTC-FX", pair[3:])
        fxp.append({"instrument_id": iid, "base_ccy": pair[:3], "quote_ccy": pair[3:],
                    "spot_lag_bdays": 1 if pair == "USDCAD" else 2, "pip_factor": 100 if pair.endswith("JPY") else 10000,
                    "rate_precision_dp": 3 if pair.endswith("JPY") else 5})

    fut_specs = [
        ("FUT001", "CF500 Index Future Dec26", "XCME", "USD", 50, "0.25", date(2026, 12, 18), "CCP-US", 18000, None, "CASH"),
        ("FUT002", "CF500 Index Future Sep26", "XCME", "USD", 50, "0.25", date(2026, 9, 18), "CCP-US", 18000, None, "CASH"),
        ("FUT003", "CF 10Y Note Future Dec26", "XCME", "USD", 1000, "0.015625", date(2026, 12, 18), "CCP-US", 2500, None, "CASH"),
        ("FUT004", "CF Euro 50 Index Future Dec26", "XEUR", "EUR", 10, "1", date(2026, 12, 18), "CCP-EU", 4200, None, "CASH"),
        ("FUT005", "CF Bund Future Dec26", "XEUR", "EUR", 1000, "0.01", date(2026, 12, 8), "CCP-EU", 3800, None, "CASH"),
        ("FUT006", "CF Euro 50 Index Future Oct26", "XEUR", "EUR", 10, "1", date(2026, 10, 16), "CCP-EU", 4200, None, "CASH"),
    ]
    for iid, name, market, ccy, mult, tick, ltd, ccp, im, und, stl in fut_specs:
        add_common(iid, "FUTURE", "LISTED_DERIV", name, market, ccy, maturity=ltd)
        derivs.append({"instrument_id": iid, "derivative_type": "FUTURE", "contract_multiplier": mult,
                       "tick_size": tick, "last_trading_day": ltd, "ccp_id": ccp, "initial_margin_per_contract": im,
                       "underlying_id": und, "option_type": None, "strike": None, "exercise_style": None,
                       "settlement_type": stl})
    px.update({"FUT001": 5850.0, "FUT002": 5800.0, "FUT003": 111.5, "FUT004": 5100.0, "FUT005": 131.2, "FUT006": 5080.0})
    opt_specs = [
        ("OPT001", "Nova Robotics Oct26 C", "XCBO", "USD", 100, "EQ001", "CALL", None, date(2026, 10, 16), "PHYSICAL"),
        ("OPT002", "Nova Robotics Oct26 P", "XCBO", "USD", 100, "EQ001", "PUT", None, date(2026, 10, 16), "PHYSICAL"),
        ("OPT003", "Lumen Semi Dec26 C", "XCBO", "USD", 100, "EQ007", "CALL", None, date(2026, 12, 18), "PHYSICAL"),
        ("OPT004", "CF500 Index Oct26 C", "XCBO", "USD", 100, "FUT001", "CALL", 5800, date(2026, 10, 16), "CASH"),
        ("OPT005", "CF500 Index Oct26 P", "XCBO", "USD", 100, "FUT001", "PUT", 5900, date(2026, 10, 16), "CASH"),
        ("OPT006", "CF Euro 50 Dec26 C", "XEUR", "EUR", 10, "FUT004", "CALL", 5100, date(2026, 12, 18), "CASH"),
    ]
    for iid, name, market, ccy, mult, und, otype, strike, exp, stl in opt_specs:
        if strike is None:
            strike = round(px[und] * (1.0 if otype == "CALL" else 1.02), 0)
        add_common(iid, "OPTION", "LISTED_DERIV", name, market, ccy, maturity=exp)
        derivs.append({"instrument_id": iid, "derivative_type": "OPTION", "contract_multiplier": mult,
                       "tick_size": "0.01", "last_trading_day": exp,
                       "ccp_id": "CCP-US" if market == "XCBO" else "CCP-EU", "initial_margin_per_contract": 0,
                       "underlying_id": und, "option_type": otype, "strike": strike, "exercise_style": "EUROPEAN",
                       "settlement_type": stl})
        px[iid] = round(px[und] * 0.025, 2)

    for iid, ccy, index, cal in [("IRS-USD", "USD", "SOFR-SIM", "USNY"), ("IRS-EUR", "EUR", "ESTR-SIM", "EUTA"),
                                 ("IRS-GBP", "GBP", "SONIA-SIM", "GBLO"), ("IRS-JPY", "JPY", "TONA-SIM", "JPTO")]:
        add_common(iid, "IRS_TEMPLATE", "IRS", f"{ccy} {index} fixed/float swap template", "OTC-RATES", ccy)
        swaps.append({"instrument_id": iid, "currency": ccy, "float_index": index, "fixed_freq_months": 6,
                      "float_freq_months": 3, "fixed_day_count": "30/360", "float_day_count": "ACT/360",
                      "payment_calendar": cal, "business_day_convention": "MODIFIED_FOLLOWING", "ccp_id": "CCP-RATES"})
    return {"Instruments": common, "Equity_Details": equities, "Bond_Details": bonds, "FX_Details": fxp,
            "Derivative_Details": derivs, "Swap_Templates": swaps, "_seed_prices": px}
