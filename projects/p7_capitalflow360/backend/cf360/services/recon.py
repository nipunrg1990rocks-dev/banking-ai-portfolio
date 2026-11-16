"""Position and cash reconciliation against custodian statements (R096)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.rules.calc import amount_tolerance, dec
from cf360.services.core import audit, raise_exception

SEC_TYPES = ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND")


def internal_positions(s: Session, as_of: date, sandbox_id=None) -> dict[tuple[str, str], Decimal]:
    sec_ids = set(s.scalars(select(m.Instrument.instrument_id).where(m.Instrument.product_type.in_(SEC_TYPES))))
    q = select(m.LedgerEntry.ledger_account, m.LedgerEntry.key, func.sum(m.LedgerEntry.amount)).where(
        m.LedgerEntry.ledger == "SEC", m.LedgerEntry.value_date <= as_of, ~m.LedgerEntry.ledger_account.contains(":"))
    q = q.where(m.LedgerEntry.sandbox_id == sandbox_id) if sandbox_id else q.where(m.LedgerEntry.sandbox_id.is_(None))
    out = {}
    for acct, key, qty in s.execute(q.group_by(m.LedgerEntry.ledger_account, m.LedgerEntry.key)):
        if key in sec_ids and dec(qty) != 0:
            out[(acct, key)] = dec(qty)
    return out


def internal_cash(s: Session, as_of: date, sandbox_id=None) -> dict[tuple[str, str], Decimal]:
    q = select(m.LedgerEntry.ledger_account, m.LedgerEntry.key, func.sum(m.LedgerEntry.amount)).where(
        m.LedgerEntry.ledger == "CASH", m.LedgerEntry.value_date <= as_of, ~m.LedgerEntry.ledger_account.contains(":"))
    q = q.where(m.LedgerEntry.sandbox_id == sandbox_id) if sandbox_id else q.where(m.LedgerEntry.sandbox_id.is_(None))
    return {(a, k): dec(v) for a, k, v in s.execute(q.group_by(m.LedgerEntry.ledger_account, m.LedgerEntry.key))}


def run(s: Session, statement_date: date) -> dict:
    s.execute(delete(m.ReconBreak).where(m.ReconBreak.statement_date == statement_date))
    summary = {}
    for rtype, internal in (("POSITION", internal_positions(s, statement_date)), ("CASH", internal_cash(s, statement_date))):
        ext = {(r.account_id, r.key): dec(r.value) for r in s.scalars(select(m.ExternalStatement).where(
            m.ExternalStatement.statement_type == rtype, m.ExternalStatement.statement_date == statement_date))}
        run_id = f"RECON-{rtype}-{statement_date}"
        compared = matched = breaks = within = 0
        for key in sorted(set(internal) | set(ext)):
            compared += 1
            i, e = internal.get(key), ext.get(key)
            if i is None or e is None:
                btype = "MISSING_INTERNAL" if i is None else "MISSING_EXTERNAL"
                diff = (e or Decimal(0)) - (i or Decimal(0))
            else:
                diff = e - i
                tol = Decimal(0) if rtype == "POSITION" else amount_tolerance(key[1])
                if diff == 0:
                    matched += 1
                    continue
                if abs(diff) <= tol:
                    within += 1
                    matched += 1
                    continue
                btype = "QTY_DIFF" if rtype == "POSITION" else "AMOUNT_DIFF"
            breaks += 1
            bid = f"BRK-{rtype}-{key[0]}-{key[1]}-{statement_date}"
            s.add(m.ReconBreak(break_id=bid, run_id=run_id, recon_type=rtype, account_id=key[0], key=key[1], internal_value=i,
                               external_value=e, difference=diff, break_type=btype, status="OPEN", statement_date=statement_date))
            raise_exception(s, "RECON_BREAK", "HIGH" if rtype == "CASH" else "MEDIUM", "RECONCILIATION",
                            f"{rtype} {btype} {key[0]} {key[1]}: internal {i} vs custodian {e} (diff {diff})",
                            ref_id=bid, rule_id="R096", error_code=btype, on=statement_date)
        rr = s.get(m.ReconRun, run_id)
        if rr is None:
            rr = m.ReconRun(run_id=run_id, statement_date=statement_date, recon_type=rtype, compared=0, matched=0, breaks=0,
                            within_tolerance=0)
            s.add(rr)
        rr.compared, rr.matched, rr.breaks, rr.within_tolerance = compared, matched, breaks, within
        summary[rtype] = {"compared": compared, "matched": matched, "breaks": breaks, "within_tolerance": within}
    audit(s, "RECON_RUN", "recon", str(statement_date), after=summary)
    s.flush()
    return summary
