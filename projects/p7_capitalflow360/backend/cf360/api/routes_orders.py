"""Orders with pre-trade checks, a simulated execution venue (partial fills) and booking from fills."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.api.deps import ApiError, Idempotency, as_of_date, require
from cf360.api.schemas import BookOrderIn, FillIn, OrderIn
from cf360.api.util import page, row, rows, translate
from cf360.clock import utcnow
from cf360.db import get_session
from cf360.rules import calc, engine
from cf360.rules.calc import dec
from cf360.services import booking, refcache
from cf360.services.core import audit, jsonable

router = APIRouter(prefix="/api/orders", tags=["orders"])
PRETRADE_RULES = {"DQ01", "DQ02", "DQ04", "R001", "R002", "R003", "R004", "R005", "R006", "R007", "R008", "R009", "R034",
                  "R050", "R052"}
DEFAULT_VENUE_CPTY = "CP01"


def sim_ts(as_of: date) -> datetime:
    """Simulated venue clock: executions happen on the business date (12:00 UTC is the same calendar day
    in every simulated market), offset by the wall-clock seconds so fills keep their order."""
    now = utcnow()
    return datetime.combine(as_of, time(12, 0)) + timedelta(seconds=now.minute * 60 + now.second, microseconds=now.microsecond)


def _venue(ref, inst: m.Instrument) -> str:
    return (inst.attrs or {}).get("execution_venue") or inst.market


def _pretrade(s: Session, order: dict, as_of) -> list[dict]:
    ref = refcache.get(s)
    inst = ref.instruments.get(order["instrument_id"])
    p = ref.price_on_or_before(order["instrument_id"], as_of) if inst else None
    px = order.get("limit_price") or (p[1] if p else Decimal(1))
    t = booking.normalise({**order, "quantity": order["order_qty"], "price": px, "trade_date": as_of,
                           "counterparty_id": order.get("counterparty_id") or DEFAULT_VENUE_CPTY,
                           "settle_ccy": inst.currency if inst else "USD", "source_trade_ref": "PRETRADE"})
    issues, _ = engine.validate(s, ref, t)
    return [i.as_dict() for i in issues if i.rule_id in PRETRADE_RULES]


@router.get("", summary="Orders with fill progress")
def list_orders(status: str | None = None, client_id: str | None = None, q: str | None = None,
                page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
                s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    stmt = select(m.Order).where(m.Order.sandbox_id.is_(None))
    if status:
        stmt = stmt.where(m.Order.status == status)
    if client_id:
        stmt = stmt.where(m.Order.client_id == client_id)
    if q:
        stmt = stmt.where(m.Order.order_id.ilike(f"%{q}%") | m.Order.instrument_id.ilike(f"%{q}%"))
    return page(s, stmt.order_by(m.Order.order_ts_utc.desc()), page_no, page_size)


@router.get("/{order_id}", summary="Order with executions and the booked trade")
def get_order(order_id: str, s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    o = s.get(m.Order, order_id)
    if o is None:
        raise ApiError(404, "E_NOT_FOUND", "Order not found")
    ex = rows(s.scalars(select(m.Execution).where(m.Execution.order_id == order_id).order_by(m.Execution.exec_ts_utc)))
    trades = rows(s.scalars(select(m.Trade).where(m.Trade.order_id == order_id)))
    fq = sum((dec(e["exec_qty"]) for e in ex), Decimal(0))
    vwap = str(calc.q_dp(sum(dec(e["exec_qty"]) * dec(e["exec_price"]) for e in ex) / fq, 6)) if fq else None
    return {"order": row(o), "executions": ex, "filled_qty": str(fq), "vwap": vwap, "trades": trades}


def _fill(s: Session, o: m.Order, fills: list[FillIn], venue: str, cpty: str, as_of: date) -> None:
    filled = dec(o.filled_qty or 0)
    for f in fills:
        if filled + f.qty > dec(o.order_qty):
            raise ApiError(422, "E_OVERFILL", f"Fill {f.qty} would exceed order quantity {o.order_qty}")
        if o.order_type == "LIMIT" and o.limit_price is not None:
            worse = f.price > o.limit_price if o.side in ("BUY", "SEC_BORROW") else f.price < o.limit_price
            if worse:
                raise ApiError(422, "E_LIMIT_VIOLATION", f"Fill price {f.price} breaches limit {o.limit_price}")
        s.add(m.Execution(exec_id="EX-" + uuid.uuid4().hex[:10].upper(), order_id=o.order_id, execution_venue=f.venue or venue,
                          counterparty_id=cpty, exec_qty=f.qty, exec_price=f.price, exec_ts_utc=sim_ts(as_of),
                          sandbox_id=o.sandbox_id))
        s.add(m.InterfaceMessage(message_id="MSG-EXEC-" + uuid.uuid4().hex[:10], system="EXCHANGE", direction="IN",
                                 message_type="EXECUTION_REPORT", correlation_id=o.order_id, ref_id=o.order_id,
                                 payload=jsonable({"qty": f.qty, "price": f.price, "venue": f.venue or venue}),
                                 status="PROCESSED", business_date=as_of, sandbox_id=o.sandbox_id))
        filled += f.qty
    o.filled_qty = filled
    o.status = "FILLED" if filled == dec(o.order_qty) else ("PARTIALLY_FILLED" if filled > 0 else "NEW")


@router.post("", status_code=201, summary="Create an order: pre-trade checks then simulated execution")
def create_order(body: OrderIn, idempotency_key: str | None = Header(default=None), s: Session = Depends(get_session),
                 user: m.User = Depends(require("order.manage")), as_of=Depends(as_of_date)):
    payload = body.model_dump(mode="python")
    idem = Idempotency(s, idempotency_key, user, payload)
    if idem.replay:
        return idem.replay[1]
    issues = _pretrade(s, payload, as_of)
    errors = [i for i in issues if i["severity"] == "ERROR"]
    oid = "ORD-" + uuid.uuid4().hex[:10].upper()
    inst = refcache.get(s).instruments.get(body.instrument_id)
    o = m.Order(order_id=oid, client_id=body.client_id, account_id=body.account_id, instrument_id=body.instrument_id,
                product_type=body.product_type, side=body.side, order_type=body.order_type, limit_price=body.limit_price,
                order_qty=body.order_qty, filled_qty=Decimal(0), order_ccy=inst.currency if inst else "USD",
                trader_id=user.username, order_ts_utc=sim_ts(as_of), status="REJECTED" if errors else "NEW",
                channel=body.channel, pretrade_result=jsonable({"issues": issues, "as_of": as_of}))
    s.add(o)
    s.flush()
    audit(s, "ORDER_CREATED", "order", oid, after=row(o))
    if not errors:
        ref = refcache.get(s)
        fills = body.simulate_fills
        if fills is None:
            refpx = ref.price_on_or_before(body.instrument_id, as_of)
            if refpx is None:
                raise ApiError(422, "E_NO_REFERENCE_PRICE", "No reference price to simulate a fill; pass simulate_fills")
            px = body.limit_price if body.order_type == "LIMIT" else refpx[1]
            lot = Decimal(int((inst.attrs or {}).get("lot_size", 1)))
            first = (body.order_qty * Decimal("0.6") / lot).to_integral_value() * lot
            fills = [FillIn(qty=q, price=px) for q in (first, body.order_qty - first) if q > 0]
        with translate(s):
            _fill(s, o, fills, _venue(ref, inst), body.counterparty_id or DEFAULT_VENUE_CPTY, as_of)
    out = {"order": row(o), "pretrade": issues}
    idem.store(201, out)
    s.commit()
    return out


@router.post("/{order_id}/fills", summary="Receive further executions from the simulated venue")
def add_fills(order_id: str, fills: list[FillIn], s: Session = Depends(get_session), user: m.User = Depends(require("order.manage")),
              as_of=Depends(as_of_date)):
    o = s.get(m.Order, order_id)
    if o is None:
        raise ApiError(404, "E_NOT_FOUND", "Order not found")
    if o.status in ("REJECTED", "CANCELLED", "BOOKED"):
        raise ApiError(409, "E_ORDER_CLOSED", f"Order is {o.status}")
    inst = refcache.get(s).instruments[o.instrument_id]
    with translate(s):
        _fill(s, o, fills, _venue(None, inst), DEFAULT_VENUE_CPTY, as_of)
        audit(s, "ORDER_FILLED", "order", order_id, after={"filled_qty": o.filled_qty, "status": o.status})
        s.commit()
    return {"order": row(o)}


@router.post("/{order_id}/cancel", summary="Cancel the unfilled remainder of an order")
def cancel_order(order_id: str, s: Session = Depends(get_session), user: m.User = Depends(require("order.manage"))):
    o = s.get(m.Order, order_id)
    if o is None:
        raise ApiError(404, "E_NOT_FOUND", "Order not found")
    if o.status in ("REJECTED", "CANCELLED", "FILLED"):
        raise ApiError(409, "E_ORDER_CLOSED", f"Order is {o.status}")
    o.status = "PARTIALLY_FILLED_DONE" if dec(o.filled_qty or 0) > 0 else "CANCELLED"
    audit(s, "ORDER_CANCELLED", "order", order_id, after={"status": o.status})
    s.commit()
    return {"order": row(o)}


@router.post("/{order_id}/book", status_code=201, summary="Book a trade from the order's fills (quantity = sum, price = VWAP)")
def book_from_order(order_id: str, body: BookOrderIn, s: Session = Depends(get_session), user: m.User = Depends(require("trade.book")),
                    as_of=Depends(as_of_date)):
    o = s.get(m.Order, order_id)
    if o is None:
        raise ApiError(404, "E_NOT_FOUND", "Order not found")
    if s.scalar(select(m.Trade.trade_id).where(m.Trade.order_id == order_id, m.Trade.status != "CANCELLED")):
        raise ApiError(409, "E_ALREADY_BOOKED", "Order already has a live trade")
    ex = list(s.scalars(select(m.Execution).where(m.Execution.order_id == order_id)))
    if not ex:
        raise ApiError(409, "E_NO_FILLS", "Order has no executions to book")
    fq = sum(dec(e.exec_qty) for e in ex)
    vwap = calc.q_dp(sum(dec(e.exec_qty) * dec(e.exec_price) for e in ex) / fq, 6)
    first = min(ex, key=lambda e: e.exec_ts_utc)
    payload = {"source_system": "OMS", "source_trade_ref": f"OMS-{order_id}", "order_id": order_id, "product_type": o.product_type,
               "instrument_id": o.instrument_id, "client_id": o.client_id, "account_id": o.account_id,
               "counterparty_id": body.counterparty_id or first.counterparty_id, "book": body.book, "trader_id": o.trader_id,
               "side": o.side, "quantity": fq, "price": vwap, "settle_ccy": body.settle_ccy or o.order_ccy,
               "trade_date": as_of, "trade_time_utc": first.exec_ts_utc, "terms": {**body.terms, "execution_venue": first.execution_venue},
               "allocations": [a.model_dump() for a in body.allocations] if body.allocations else None}
    with translate(s):
        t = booking.book(s, payload, (user.username, user.role), o.sandbox_id, reject_log=False)
        if o.status == "PARTIALLY_FILLED":
            o.status = "PARTIALLY_FILLED_DONE"
        s.commit()
    return {"trade": row(t), "vwap": str(vwap), "filled_qty": str(fq)}
