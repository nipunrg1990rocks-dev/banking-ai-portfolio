"""Load the Excel baseline into PostgreSQL and run processing to the statement date.

    python -m cf360.seed            # reset schema data, load, process, reconcile
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.config import settings
from cf360.services import booking, refcache
from cf360.services.core import audit, post

STATEMENT_DATE = date(2026, 11, 6)
OPENING_DATE = date(2026, 9, 11)
ROLES = [("trader1", "Tara Trader", "TRADER"), ("trader2", "Theo Trader", "TRADER"),
         ("mo1", "Maya Middle-Office", "MIDDLE_OFFICE"), ("settle1", "Sam Settlements", "SETTLEMENTS"),
         ("recon1", "Rita Reconciliation", "RECONCILIATION"), ("risk1", "Raj Risk & Control", "RISK"),
         ("checker1", "Chris Checker", "CHECKER"), ("checker2", "Cleo Checker", "CHECKER"), ("admin1", "Ada Admin", "ADMIN")]


def hash_password(pw: str, salt: str) -> str:
    return "pbkdf2$" + salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()


def verify_password(pw: str, stored: str) -> bool:
    _, salt, _h = stored.split("$")
    return hash_password(pw, salt) == stored


def read(path: Path) -> dict[str, list[dict]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        if ws.title in ("Dictionary", "Provenance"):
            continue
        rows = list(ws.iter_rows(values_only=True))
        cols = rows[0] if rows else []
        out[ws.title] = [dict(zip(cols, r, strict=False)) for r in rows[1:]]
    return out


def _d(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _dec(v):
    if v is None:
        return None
    return Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v))


TABLES_IN_ORDER = ["test_results", "test_runs", "job_runs", "dq_results", "processing_runs", "booking_rejections",
                   "idempotency_keys", "interface_messages", "controlled_outcomes", "external_statements", "recon_breaks",
                   "recon_runs", "approvals", "exceptions", "lifecycle_events", "ledger_entries", "settlement_instructions",
                   "clearing_records", "confirmations", "allocations", "trade_versions", "trades", "executions", "orders",
                   "corporate_actions", "config_params", "fx_forward_points", "rate_fixings", "fx_rates", "prices", "ssis",
                   "settlement_conventions", "calendars", "holidays", "markets", "instruments", "commission_schedules",
                   "accounts", "counterparties", "clients", "system_clock", "users"]


def reset(s: Session) -> None:
    """Remove business data (the audit trail is append-only and is kept; it is tagged by correlation id)."""
    for t in TABLES_IN_ORDER:
        s.execute(text(f"DELETE FROM {t}"))
    s.commit()


def load_reference(s: Session, data_dir: Path) -> None:
    x = data_dir / "excel"
    for u, name, role in ROLES:
        s.add(m.User(username=u, display_name=name, role=role, password_hash=hash_password(settings.demo_password, u)))
    s.add(m.SystemClock(id=1, business_date=OPENING_DATE, last_processed=OPENING_DATE))
    c = read(x / "Clients.xlsx")
    for r in c["Clients"]:
        s.add(m.Client(**{k: r[k] for k in ("client_id", "legal_name", "short_name", "lei", "client_type", "client_category",
                                            "country", "base_ccy", "status", "risk_rating", "netting_agreement",
                                            "source_system", "source_record_id")},
                       kyc_review_date=_d(r["kyc_review_date"]), kyc_expiry_date=_d(r["kyc_expiry_date"]),
                       exposure_limit_usd=_dec(r["exposure_limit_usd"])))
    for r in c["Counterparties"]:
        s.add(m.Counterparty(**{k: r[k] for k in ("counterparty_id", "legal_name", "counterparty_type", "country", "lei",
                                                  "netting_agreement", "status")}))
    s.flush()
    a = read(x / "Accounts.xlsx")
    for r in a["Accounts"]:
        s.add(m.Account(account_id=r["account_id"], client_id=r["client_id"], account_name=r["account_name"],
                        account_type=r["account_type"], base_ccy=r["base_ccy"], permitted_products=r["permitted_products"],
                        custodian_id=r["custodian_id"], partial_settlement_allowed=r["partial_settlement_allowed"] == "Y",
                        credit_line_usd=_dec(r["credit_line_usd"]), status=r["status"]))
    s.flush()
    for r in a["Commission_Schedules"]:
        s.add(m.CommissionSchedule(schedule_id=r["schedule_id"], account_id=r["account_id"], product_group=r["product_group"],
                                   commission_bps=_dec(r["commission_bps"]), min_commission_usd=_dec(r["min_commission_usd"]),
                                   effective_from=_d(r["effective_from"]), effective_to=_d(r["effective_to"])))
    ins = read(x / "Instruments.xlsx")
    details = {}
    for sheet in ("Equity_Details", "Bond_Details", "FX_Details", "Derivative_Details", "Swap_Templates"):
        for r in ins[sheet]:
            details[r["instrument_id"]] = {k: (v.date().isoformat() if isinstance(v, datetime) else v)
                                           for k, v in r.items() if k != "instrument_id"}
    for r in ins["Instruments"]:
        s.add(m.Instrument(instrument_id=r["instrument_id"], product_type=r["product_type"], product_group=r["product_group"],
                           name=r["name"], synthetic_isin=r["synthetic_isin"], market=r["market"], currency=r["currency"],
                           status=r["status"], maturity_date=_d(r["maturity_date"]), attrs=details.get(r["instrument_id"], {})))
    cal = read(x / "Business_Calendars.xlsx")
    for r in cal["Calendars"]:
        s.add(m.CalendarDef(**r))
    for r in cal["Holidays"]:
        s.add(m.Holiday(calendar_code=r["calendar_code"], holiday_date=_d(r["holiday_date"]), holiday_name=r["holiday_name"]))
    for r in cal["Markets"]:
        s.add(m.Market(market=r["market"], calendar_code=r["calendar_code"], timezone=r["timezone"], currency=r["currency"],
                       settlement_location=r["settlement_location"], exchange_fee_bps=_dec(r["exchange_fee_bps"]),
                       stamp_duty_bps=_dec(r["stamp_duty_bps"])))
    for r in cal["Settlement_Conventions"]:
        s.add(m.SettlementConvention(**{**r, "effective_from": _d(r["effective_from"]), "effective_to": _d(r["effective_to"])}))
    for r in read(x / "Settlement_Instructions.xlsx")["SSIs"]:
        s.add(m.SSI(**{k: r[k] for k in ("ssi_id", "owner_type", "owner_id", "currency", "settlement_location", "agent_code",
                                         "safekeeping_account", "cash_account", "status", "version")},
                    effective_from=_d(r["effective_from"]), effective_to=_d(r["effective_to"])))
    px = read(x / "Prices_FX_Rates.xlsx")
    s.bulk_insert_mappings(m.Price, [{"instrument_id": r["instrument_id"], "price_date": _d(r["price_date"]),
                                      "close_price": _dec(r["close_price"]), "price_type": r["price_type"]} for r in px["Prices"]])
    s.bulk_insert_mappings(m.FxRate, [{"currency": r["currency"], "rate_date": _d(r["rate_date"]),
                                       "usd_per_unit": _dec(r["usd_per_unit"])} for r in px["FX_Rates"]])
    s.bulk_insert_mappings(m.RateFixing, [{"rate_index": r["rate_index"], "fixing_date": _d(r["fixing_date"]),
                                           "rate_pct": _dec(r["rate_pct"])} for r in px["Rate_Fixings"]])
    s.bulk_insert_mappings(m.ForwardPoints, [{"pair": r["pair"], "rate_date": _d(r["rate_date"]), "tenor_months": r["tenor_months"],
                                              "forward_points": _dec(r["forward_points"])} for r in px["FX_Forward_Points"]])
    for p, v, desc in [("MATCH_AMOUNT_TOLERANCE", "1.00", "Confirmation amount tolerance in major units (JPY x100)"),
                       ("REPO_MTA", "250000", "Repo minimum transfer amount"),
                       ("RECON_CASH_TOLERANCE", "1.00", "Cash reconciliation tolerance (JPY x100)"),
                       ("STALE_PRICE_BDAYS", "3", "Maximum age of a reference price in business days")]:
        s.add(m.ConfigParam(param=p, scope="*", effective_from=date(2024, 1, 1), effective_to=None, value=v, description=desc))
    ca = read(x / "Corporate_Actions.xlsx")
    for r in ca["Dividends"]:
        s.add(m.CorporateAction(ca_id=r["ca_id"], instrument_id=r["instrument_id"], event_type="CASH_DIVIDEND",
                                ex_date=_d(r["ex_date"]), record_date=_d(r["record_date"]), pay_date=_d(r["pay_date"]),
                                rate=_dec(r["gross_rate_per_share"]), currency=r["currency"], status=r["status"]))
    for r in ca["Coupons"]:
        s.add(m.CorporateAction(ca_id=r["ca_id"], instrument_id=r["instrument_id"], event_type="COUPON", ex_date=None,
                                record_date=_d(r["record_date"]), pay_date=_d(r["pay_date"]), rate=_dec(r["amount_per_100"]),
                                currency=r["currency"], status=r["status"]))
    s.commit()
    refcache.invalidate()


def load_opening(s: Session, data_dir: Path) -> None:
    pc = read(data_dir / "excel" / "Positions_Cash.xlsx")
    for r in pc["Opening_Positions"]:
        q = _dec(r["quantity"])
        post(s, f"OPEN:SEC:{r['account_id']}:{r['instrument_id']}", "SEC",
             [(r["account_id"], r["instrument_id"], q), ("OPENING:BALANCE", r["instrument_id"], -q)], _d(r["as_of_date"]), "OPENING")
    for r in pc["Opening_Cash"]:
        b = _dec(r["balance"])
        post(s, f"OPEN:CASH:{r['account_id']}:{r['currency']}", "CASH",
             [(r["account_id"], r["currency"], b), ("OPENING:BALANCE", r["currency"], -b)], _d(r["as_of_date"]), "OPENING")
    s.commit()


def load_front_office(s: Session, data_dir: Path) -> dict:
    x = data_dir / "excel"
    for r in read(x / "Orders.xlsx")["Orders"]:
        s.add(m.Order(order_id=r["order_id"], client_id=r["client_id"], account_id=r["account_id"], instrument_id=r["instrument_id"],
                      product_type=r["product_type"], side=r["side"], order_type=r["order_type"], limit_price=_dec(r["limit_price"]),
                      order_qty=_dec(r["order_qty"]), order_ccy=r["order_ccy"], trader_id=r["trader_id"],
                      order_ts_utc=r["order_ts_utc"], status=r["status"], channel=r["channel"]))
    s.flush()
    for r in read(x / "Executions.xlsx")["Executions"]:
        s.add(m.Execution(exec_id=r["exec_id"], order_id=r["order_id"], execution_venue=r["execution_venue"],
                          counterparty_id=r["counterparty_id"], exec_qty=_dec(r["exec_qty"]), exec_price=_dec(r["exec_price"]),
                          exec_ts_utc=r["exec_ts_utc"]))
    s.commit()
    allocs: dict[str, list[dict]] = {}
    for r in read(x / "Allocations.xlsx")["Allocations"]:
        allocs.setdefault(r["trade_id"], []).append({"allocation_id": r["allocation_id"], "alloc_seq": r["alloc_seq"],
                                                     "account_id": r["account_id"], "alloc_qty": _dec(r["alloc_qty"])})
    ext = read(x / "External_Reconciliation.xlsx")
    for r in ext["Settlement_Status"]:
        s.add(m.ControlledOutcome(key=r["allocation_id"], system="CUSTODIAN", status=r["status"], reason_code=r["reason_code"],
                                  resolved_date=_d(r["resolved_date"])))
    s.commit()
    trades = read(x / "Trades.xlsx")["Trades"]
    booked, rejected = 0, []
    actor = ("system", "SYSTEM")
    for r in sorted(trades, key=lambda r: (r["trade_time_utc"], r["trade_id"])):
        payload = {k: (_dec(v) if isinstance(v, float) else v) for k, v in r.items()}
        payload["allocations"] = sorted(allocs.get(r["trade_id"], []), key=lambda a: a["alloc_seq"])
        try:
            with s.begin_nested():
                booking.book(s, payload, actor)
            booked += 1
        except booking.BookingError as e:
            rejected.append({"trade_id": r["trade_id"], "errors": e.issues})
            s.add(m.BookingRejection(source_trade_ref=r["source_trade_ref"], ingest_batch=r["ingest_batch"], errors=e.issues,
                                     payload={"trade_id": r["trade_id"]}))
    s.commit()
    for r in ext["Confirmations"]:
        s.add(m.Confirmation(confirmation_id=r["confirmation_id"], trade_id=r["trade_id"], counterparty_id=r["counterparty_id"],
                             cp_trade_ref=r["cp_trade_ref"], quantity=_dec(r["quantity"]), price=_dec(r["price"]),
                             settle_amount=_dec(r["settle_amount"]), settle_ccy=r["settle_ccy"], settle_date=_d(r["settle_date"]),
                             received_ts_utc=r["received_ts_utc"]))
        s.add(m.InterfaceMessage(message_id=f"MSG-CONF-{r['confirmation_id']}", system="BROKER", direction="IN",
                                 message_type="CONFIRMATION", sequence_no=int(r["confirmation_id"][3:]),
                                 correlation_id=r["trade_id"], ref_id=r["trade_id"],
                                 payload={"cp_trade_ref": r["cp_trade_ref"], "price": str(r["price"]), "amount": str(r["settle_amount"])},
                                 status="PROCESSED", business_date=_d(r["received_ts_utc"])))
    for r in ext["Custody_Positions"]:
        s.add(m.ExternalStatement(statement_type="POSITION", statement_date=_d(r["statement_date"]), account_id=r["account_id"],
                                  key=r["instrument_id"], value=_dec(r["quantity"]), source_record_id=r["source_record_id"]))
    for r in ext["Custody_Cash"]:
        s.add(m.ExternalStatement(statement_type="CASH", statement_date=_d(r["statement_date"]), account_id=r["account_id"],
                                  key=r["currency"], value=_dec(r["balance"]), source_record_id=r["source_record_id"]))
    s.commit()
    return {"booked": booked, "rejected": rejected}


def seed(s: Session, process_to: date | None = STATEMENT_DATE, data_dir: Path | None = None) -> dict:
    data_dir = data_dir or settings.data_dir
    reset(s)
    load_reference(s, data_dir)
    load_opening(s, data_dir)
    fo = load_front_office(s, data_dir)
    audit(s, "SEED_LOADED", "dataset", "baseline", details={"booked": fo["booked"], "rejected": len(fo["rejected"])})
    s.commit()
    result = {"booked": fo["booked"], "rejected": fo["rejected"]}
    if process_to:
        from cf360.services import processing, recon
        processing.run_through(s, process_to)
        clock = s.get(m.SystemClock, 1)
        clock.business_date = process_to
        s.commit()
        result["recon"] = recon.run(s, process_to)
        s.commit()
    return result


_ = (select, delete)
