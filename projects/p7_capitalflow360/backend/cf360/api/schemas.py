"""Request schemas (pydantic v2). Business rules are enforced by the rule engine, not here:
schemas only check shape and types, so a structurally valid but wrong trade still reaches the
engine and gets the documented rule id and error code back."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProductType = Literal["EQUITY", "ETF", "GOVT_BOND", "CORP_BOND", "FX_SPOT", "FX_FORWARD", "FUTURE", "OPTION", "IRS",
                      "REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"]
Side = Literal["BUY", "SELL", "PAY_FIXED", "RECEIVE_FIXED", "REPO", "REVERSE_REPO", "LEND", "BORROW"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class AllocationIn(Strict):
    account_id: str
    alloc_qty: Decimal = Field(gt=0)


class TradeIn(Strict):
    """A trade booking. Asset-specific economic terms go in ``terms`` (see the data dictionary)."""
    source_trade_ref: str | None = Field(default=None, max_length=60)
    source_system: str | None = "API"
    order_id: str | None = None
    product_type: ProductType
    instrument_id: str
    client_id: str
    account_id: str
    counterparty_id: str
    book: str | None = None
    trader_id: str | None = None
    side: Side
    quantity: Decimal
    price: Decimal
    trade_ccy: str | None = Field(default=None, min_length=3, max_length=3)
    settle_ccy: str = Field(min_length=3, max_length=3)
    trade_date: date
    settlement_date: date | None = None
    end_date: date | None = None
    trade_time_utc: datetime | None = None
    ingest_batch: str | None = None
    terms: dict[str, Any] = Field(default_factory=dict)
    allocations: list[AllocationIn] | None = None


class AmendIn(Strict):
    changes: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=500)


class CancelIn(Strict):
    reason: str = Field(min_length=3, max_length=500)
    rebook: dict[str, Any] | None = None


class TransitionIn(Strict):
    target: str
    reason: str = Field(default="manual transition", max_length=500)


class ReasonIn(Strict):
    reason: str = Field(min_length=3, max_length=500)


class FillIn(Strict):
    qty: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    venue: str | None = None


class OrderIn(Strict):
    client_id: str
    account_id: str
    instrument_id: str
    product_type: ProductType
    side: Side
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: Decimal | None = None
    order_qty: Decimal = Field(gt=0)
    counterparty_id: str | None = None
    channel: Literal["DMA", "VOICE", "ALGO", "API"] = "API"
    simulate_fills: list[FillIn] | None = Field(default=None, description="Deterministic fills for the simulated venue; "
                                                "omit to fill at the reference price in two clips (60/40)")


class BookOrderIn(Strict):
    counterparty_id: str | None = None
    book: str | None = None
    settle_ccy: str | None = None
    allocations: list[AllocationIn] | None = None
    terms: dict[str, Any] = Field(default_factory=dict)


class DecisionIn(Strict):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class SSIIn(Strict):
    owner_type: Literal["CLIENT", "COUNTERPARTY", "FIRM"]
    owner_id: str
    currency: str = Field(min_length=3, max_length=3)
    settlement_location: str
    agent_code: str = "SIMBNEWXX"
    safekeeping_account: str | None = None
    cash_account: str | None = None
    effective_from: date
    effective_to: date | None = None
    reason: str = Field(min_length=3)


class ClientChangeIn(Strict):
    changes: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=3)


class ExceptionActionIn(Strict):
    action: Literal["ASSIGN", "START", "RESOLVE", "RETRY", "COMMENT", "ESCALATE"]
    assignee: str | None = None
    note: str | None = Field(default=None, max_length=1000)


class InterfaceMessageIn(Strict):
    message_id: str
    message_type: Literal["CONFIRMATION", "CLEARING_ACK", "SETTLEMENT_CONFIRMATION", "PAYMENT_ACK"]
    sequence_no: int = Field(ge=1)
    ref_id: str | None = None
    business_date: date | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ProcessIn(Strict):
    business_date: date | None = Field(default=None, description="Run end-of-day processing up to this date (inclusive)")


class ClockIn(Strict):
    business_date: date


class WriteOffIn(Strict):
    note: str = Field(min_length=3)


class SandboxIn(Strict):
    sandbox_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{3,60}$")


class TestRunIn(BaseModel):
    run_id: str
    label: str
    environment: str
    started_at: datetime | None = None
    defects_enabled: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    replace: bool = True


class JobRunsIn(BaseModel):
    job_runs: list[dict[str, Any]] = Field(default_factory=list)
    dq_results: list[dict[str, Any]] = Field(default_factory=list)
