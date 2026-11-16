"""Relational model. Monetary values are NUMERIC; quantities NUMERIC(28,6)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cf360.db import Base

AMT = Numeric(28, 8)
QTY = Numeric(28, 6)
RATE = Numeric(20, 10)


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemClock(Base):
    __tablename__ = "system_clock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    business_date: Mapped[date] = mapped_column(Date)
    last_processed: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ----------------------------------------------------------------------------- reference data
class Client(Base):
    __tablename__ = "clients"
    client_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(120))
    short_name: Mapped[str] = mapped_column(String(40))
    lei: Mapped[str | None] = mapped_column(String(20))
    client_type: Mapped[str] = mapped_column(String(30))
    client_category: Mapped[str] = mapped_column(String(30))
    country: Mapped[str] = mapped_column(String(2))
    base_ccy: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    kyc_review_date: Mapped[date | None] = mapped_column(Date)
    kyc_expiry_date: Mapped[date] = mapped_column(Date)
    risk_rating: Mapped[str] = mapped_column(String(10))
    exposure_limit_usd: Mapped[Decimal] = mapped_column(AMT)
    netting_agreement: Mapped[str] = mapped_column(String(1))
    source_system: Mapped[str | None] = mapped_column(String(30))
    source_record_id: Mapped[str | None] = mapped_column(String(60))
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Counterparty(Base):
    __tablename__ = "counterparties"
    counterparty_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(120))
    counterparty_type: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2))
    lei: Mapped[str | None] = mapped_column(String(20))
    netting_agreement: Mapped[str] = mapped_column(String(1))
    status: Mapped[str] = mapped_column(String(20))


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), index=True)
    account_name: Mapped[str] = mapped_column(String(80))
    account_type: Mapped[str] = mapped_column(String(20))
    base_ccy: Mapped[str] = mapped_column(String(3))
    permitted_products: Mapped[str] = mapped_column(String(120))
    custodian_id: Mapped[str] = mapped_column(String(10))
    partial_settlement_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    credit_line_usd: Mapped[Decimal] = mapped_column(AMT, default=0)
    status: Mapped[str] = mapped_column(String(20))
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CommissionSchedule(Base):
    __tablename__ = "commission_schedules"
    schedule_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    product_group: Mapped[str] = mapped_column(String(20))
    commission_bps: Mapped[Decimal] = mapped_column(RATE)
    min_commission_usd: Mapped[Decimal] = mapped_column(AMT)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Instrument(Base):
    __tablename__ = "instruments"
    instrument_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    product_type: Mapped[str] = mapped_column(String(20), index=True)
    product_group: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(120))
    synthetic_isin: Mapped[str | None] = mapped_column(String(12))
    market: Mapped[str] = mapped_column(String(12))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    maturity_date: Mapped[date | None] = mapped_column(Date)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Market(Base):
    __tablename__ = "markets"
    market: Mapped[str] = mapped_column(String(12), primary_key=True)
    calendar_code: Mapped[str] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    settlement_location: Mapped[str] = mapped_column(String(20))
    exchange_fee_bps: Mapped[Decimal] = mapped_column(RATE)
    stamp_duty_bps: Mapped[Decimal] = mapped_column(RATE)


class Holiday(Base):
    __tablename__ = "holidays"
    calendar_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    holiday_date: Mapped[date] = mapped_column(Date, primary_key=True)
    holiday_name: Mapped[str] = mapped_column(String(80))


class CalendarDef(Base):
    __tablename__ = "calendars"
    calendar_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    description: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(40))


class SettlementConvention(Base):
    __tablename__ = "settlement_conventions"
    convention_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    product_types: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(12))
    offset_bdays: Mapped[int] = mapped_column(Integer)
    calendar_basis: Mapped[str] = mapped_column(String(20))
    cutoff_local_time: Mapped[str] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_reference: Mapped[str] = mapped_column(String(200))
    simplification_note: Mapped[str | None] = mapped_column(String(200))


class SSI(Base):
    __tablename__ = "ssis"
    ssi_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    settlement_location: Mapped[str] = mapped_column(String(20))
    agent_code: Mapped[str] = mapped_column(String(20))
    safekeeping_account: Mapped[str | None] = mapped_column(String(40))
    cash_account: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class Price(Base):
    __tablename__ = "prices"
    instrument_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    price_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close_price: Mapped[Decimal] = mapped_column(RATE)
    price_type: Mapped[str] = mapped_column(String(20))


class FxRate(Base):
    __tablename__ = "fx_rates"
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    usd_per_unit: Mapped[Decimal] = mapped_column(RATE)


class RateFixing(Base):
    __tablename__ = "rate_fixings"
    rate_index: Mapped[str] = mapped_column(String(20), primary_key=True)
    fixing_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate_pct: Mapped[Decimal] = mapped_column(RATE)


class ForwardPoints(Base):
    __tablename__ = "fx_forward_points"
    pair: Mapped[str] = mapped_column(String(6), primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tenor_months: Mapped[int] = mapped_column(Integer, primary_key=True)
    forward_points: Mapped[Decimal] = mapped_column(RATE)


class ConfigParam(Base):
    """Effective-dated configuration (tolerances, thresholds)."""
    __tablename__ = "config_params"
    param: Mapped[str] = mapped_column(String(60), primary_key=True)
    scope: Mapped[str] = mapped_column(String(30), primary_key=True, default="*")
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    value: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    ca_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    ex_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date] = mapped_column(Date)
    pay_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(RATE)  # per share (dividend) or per 100 (coupon)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


# ----------------------------------------------------------------------------- front office
class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40), index=True)
    account_id: Mapped[str] = mapped_column(String(40))
    instrument_id: Mapped[str] = mapped_column(String(40))
    product_type: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(20))
    order_type: Mapped[str] = mapped_column(String(10))
    limit_price: Mapped[Decimal | None] = mapped_column(RATE)
    order_qty: Mapped[Decimal] = mapped_column(QTY)
    filled_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    order_ccy: Mapped[str] = mapped_column(String(3))
    trader_id: Mapped[str] = mapped_column(String(40))
    order_ts_utc: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30))
    channel: Mapped[str | None] = mapped_column(String(10))
    pretrade_result: Mapped[dict | None] = mapped_column(JSON)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class Execution(Base):
    __tablename__ = "executions"
    exec_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), index=True)
    execution_venue: Mapped[str] = mapped_column(String(12))
    counterparty_id: Mapped[str] = mapped_column(String(40))
    exec_qty: Mapped[Decimal] = mapped_column(QTY)
    exec_price: Mapped[Decimal] = mapped_column(RATE)
    exec_ts_utc: Mapped[datetime] = mapped_column(DateTime)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class Trade(Base):
    __tablename__ = "trades"
    trade_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), index=True)
    source_system: Mapped[str] = mapped_column(String(30))
    source_trade_ref: Mapped[str] = mapped_column(String(60), index=True)
    order_id: Mapped[str | None] = mapped_column(String(40), index=True)
    product_group: Mapped[str] = mapped_column(String(20), index=True)
    product_type: Mapped[str] = mapped_column(String(20), index=True)
    instrument_id: Mapped[str] = mapped_column(String(40), index=True)
    client_id: Mapped[str] = mapped_column(String(40), index=True)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    book: Mapped[str] = mapped_column(String(30), index=True)
    trader_id: Mapped[str] = mapped_column(String(40))
    counterparty_id: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    price: Mapped[Decimal] = mapped_column(RATE)
    trade_ccy: Mapped[str] = mapped_column(String(3))
    settle_ccy: Mapped[str] = mapped_column(String(3), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    trade_time_utc: Mapped[datetime] = mapped_column(DateTime)
    market: Mapped[str] = mapped_column(String(12))
    settlement_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)  # asset-specific economic terms
    economics: Mapped[dict] = mapped_column(JSON, default=dict)  # computed economics (strings)
    gross_amount: Mapped[Decimal | None] = mapped_column(AMT)
    net_amount: Mapped[Decimal | None] = mapped_column(AMT)
    usd_equivalent: Mapped[Decimal | None] = mapped_column(AMT)
    match_status: Mapped[str | None] = mapped_column(String(30))
    flags: Mapped[list] = mapped_column(JSON, default=list)
    ingest_batch: Mapped[str | None] = mapped_column(String(40))
    rebook_of: Mapped[str | None] = mapped_column(String(40))
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_trade_dupkey", "client_id", "instrument_id", "side", "trade_date"),)


class TradeVersion(Base):
    __tablename__ = "trade_versions"
    trade_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    change_type: Mapped[str] = mapped_column(String(30))
    changed_by: Mapped[str] = mapped_column(String(40))
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Allocation(Base):
    __tablename__ = "allocations"
    allocation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.trade_id"), index=True)
    alloc_seq: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    alloc_qty: Mapped[Decimal] = mapped_column(QTY)
    settle_amount: Mapped[Decimal] = mapped_column(AMT, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Confirmation(Base):
    __tablename__ = "confirmations"
    confirmation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(40), index=True)
    counterparty_id: Mapped[str] = mapped_column(String(40))
    cp_trade_ref: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    price: Mapped[Decimal] = mapped_column(RATE)
    settle_amount: Mapped[Decimal | None] = mapped_column(AMT)
    settle_ccy: Mapped[str] = mapped_column(String(3))
    settle_date: Mapped[date] = mapped_column(Date)
    received_ts_utc: Mapped[datetime] = mapped_column(DateTime)
    match_result: Mapped[str | None] = mapped_column(String(30))
    match_detail: Mapped[dict | None] = mapped_column(JSON)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class ClearingRecord(Base):
    __tablename__ = "clearing_records"
    clearing_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(40), index=True)
    ccp_id: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    novation_ref: Mapped[str] = mapped_column(String(60))
    business_date: Mapped[date] = mapped_column(Date)


class SettlementInstruction(Base):
    __tablename__ = "settlement_instructions"
    instruction_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(40), index=True)
    allocation_id: Mapped[str | None] = mapped_column(String(40), index=True)
    leg: Mapped[str] = mapped_column(String(20))
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    instrument_id: Mapped[str | None] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10))  # RECEIVE / DELIVER / PAY / COLLECT / FX
    sec_qty: Mapped[Decimal] = mapped_column(QTY, default=0)  # signed (+ receive)
    cash_amount: Mapped[Decimal] = mapped_column(AMT, default=0)  # signed (+ receive)
    currency: Mapped[str] = mapped_column(String(3))
    fx_legs: Mapped[list | None] = mapped_column(JSON)
    settle_date: Mapped[date] = mapped_column(Date, index=True)
    settlement_location: Mapped[str] = mapped_column(String(20))
    our_ssi_id: Mapped[str | None] = mapped_column(String(40))
    cpty_ssi_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)  # PENDING/SETTLED/PARTIAL/FAILED/CANCELLED
    settled_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    settled_cash: Mapped[Decimal] = mapped_column(AMT, default=0)
    settled_on: Mapped[date | None] = mapped_column(Date)
    fail_reason: Mapped[str | None] = mapped_column(String(40))
    first_fail_date: Mapped[date | None] = mapped_column(Date)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    partial_count: Mapped[int] = mapped_column(Integer, default=0)
    cutoff_missed: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class LedgerEntry(Base):
    """Double-entry ledger for cash (key=currency) and securities (key=instrument).
    (source_event_id, leg_seq) is unique, which makes posting idempotent."""
    __tablename__ = "ledger_entries"
    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    posting_id: Mapped[str] = mapped_column(String(100), index=True)
    source_event_id: Mapped[str] = mapped_column(String(100))
    leg_seq: Mapped[int] = mapped_column(Integer)
    ledger: Mapped[str] = mapped_column(String(4))  # CASH or SEC
    ledger_account: Mapped[str] = mapped_column(String(60), index=True)  # client account or contra account
    key: Mapped[str] = mapped_column(String(40), index=True)  # currency or instrument id
    amount: Mapped[Decimal] = mapped_column(AMT)
    value_date: Mapped[date] = mapped_column(Date, index=True)
    entry_type: Mapped[str] = mapped_column(String(30))
    trade_id: Mapped[str | None] = mapped_column(String(40), index=True)
    reversal_of: Mapped[str | None] = mapped_column(String(100))
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("source_event_id", "leg_seq", name="uq_ledger_event_leg"),)


class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"
    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    trade_id: Mapped[str | None] = mapped_column(String(40), index=True)
    allocation_id: Mapped[str | None] = mapped_column(String(40))
    account_id: Mapped[str | None] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal | None] = mapped_column(AMT)
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class ExceptionItem(Base):
    __tablename__ = "exceptions"
    exception_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    exception_type: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), index=True)
    owner_role: Mapped[str] = mapped_column(String(30))
    assigned_to: Mapped[str | None] = mapped_column(String(40))
    trade_id: Mapped[str | None] = mapped_column(String(40), index=True)
    ref_id: Mapped[str | None] = mapped_column(String(60))
    rule_id: Mapped[str | None] = mapped_column(String(10))
    error_code: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    opened_business_date: Mapped[date] = mapped_column(Date)
    resolved_business_date: Mapped[date | None] = mapped_column(Date)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    approval_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(30))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    maker: Mapped[str] = mapped_column(String(40))
    checker: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class ReconRun(Base):
    __tablename__ = "recon_runs"
    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    statement_date: Mapped[date] = mapped_column(Date)
    recon_type: Mapped[str] = mapped_column(String(20))
    compared: Mapped[int] = mapped_column(Integer)
    matched: Mapped[int] = mapped_column(Integer)
    breaks: Mapped[int] = mapped_column(Integer)
    within_tolerance: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReconBreak(Base):
    __tablename__ = "recon_breaks"
    break_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(60), index=True)
    recon_type: Mapped[str] = mapped_column(String(20))
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(40))
    internal_value: Mapped[Decimal | None] = mapped_column(AMT)
    external_value: Mapped[Decimal | None] = mapped_column(AMT)
    difference: Mapped[Decimal] = mapped_column(AMT)
    break_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    statement_date: Mapped[date] = mapped_column(Date)


class ExternalStatement(Base):
    __tablename__ = "external_statements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    statement_type: Mapped[str] = mapped_column(String(20))  # POSITION / CASH
    statement_date: Mapped[date] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(AMT)
    source_record_id: Mapped[str] = mapped_column(String(80))


class InterfaceMessage(Base):
    """Simulated external interface traffic (exchange, broker, CCP, custodian, payment system)."""
    __tablename__ = "interface_messages"
    message_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    system: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # IN / OUT
    message_type: Mapped[str] = mapped_column(String(40))
    sequence_no: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(String(60), index=True)
    ref_id: Mapped[str | None] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))  # PROCESSED / PARKED / IGNORED_DUPLICATE / PENDING
    business_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class ControlledOutcome(Base):
    """Controlled outcomes for the simulated custodian/CCP (loaded from External_Reconciliation.xlsx)."""
    __tablename__ = "controlled_outcomes"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)  # allocation id (+ -E/-F)
    system: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(40))
    resolved_date: Mapped[date | None] = mapped_column(Date)


class AuditEvent(Base):
    """Append-only (enforced by a database trigger created in the migration)."""
    __tablename__ = "audit_events"
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    business_date: Mapped[date | None] = mapped_column(Date)
    actor: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    details: Mapped[dict | None] = mapped_column(JSON)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    actor: Mapped[str] = mapped_column(String(40))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BookingRejection(Base):
    __tablename__ = "booking_rejections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_trade_ref: Mapped[str | None] = mapped_column(String(60))
    ingest_batch: Mapped[str | None] = mapped_column(String(40))
    errors: Mapped[list] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    sandbox_id: Mapped[str | None] = mapped_column(String(60), index=True)


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False
    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    environment: Mapped[str] = mapped_column(String(200))
    defects_enabled: Mapped[str] = mapped_column(String(60), default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    project: Mapped[str] = mapped_column(String(40), index=True)  # api / chromium / firefox / webkit
    scenario_id: Mapped[str | None] = mapped_column(String(60), index=True)
    case_id: Mapped[str | None] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), index=True)  # passed/failed/skipped/flaky
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    defect_id: Mapped[str | None] = mapped_column(String(10))


class JobRun(Base):
    __tablename__ = "job_runs"
    job_run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    environment: Mapped[str] = mapped_column(String(30))
    task: Mapped[str] = mapped_column(String(40))
    batch_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    rows_quarantined: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class DQResult(Base):
    __tablename__ = "dq_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(40), index=True)
    table_name: Mapped[str] = mapped_column(String(60))
    rule_id: Mapped[str] = mapped_column(String(10))
    dimension: Mapped[str] = mapped_column(String(30))  # completeness/validity/uniqueness/integrity/schema
    checked: Mapped[int] = mapped_column(Integer)
    failed: Mapped[int] = mapped_column(Integer)
    environment: Mapped[str] = mapped_column(String(30))
