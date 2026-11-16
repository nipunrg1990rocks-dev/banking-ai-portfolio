"""Typed Excel writer: data sheets + Dictionary + Provenance, with byte-reproducible output."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

FIXED_TS = datetime(2026, 11, 16, 0, 0, 0)
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)

TYPE_FORMAT = {"DATE": "yyyy-mm-dd", "TIMESTAMP": "yyyy-mm-dd hh:mm:ss", "INTEGER": "#,##0",
               "DECIMAL": "#,##0.00########", "STRING": "@"}

# Column descriptions shared by all workbooks (data dictionary)
DOCS = {
    "trade_id": "Platform trade identifier (primary key of Trades)", "order_id": "Order identifier (Orders)",
    "exec_id": "Execution (fill) identifier", "allocation_id": "Allocation identifier", "alloc_seq": "Allocation sequence within the trade",
    "client_id": "Client identifier (Clients)", "account_id": "Account identifier (Accounts)",
    "instrument_id": "Instrument identifier (Instruments)", "counterparty_id": "Counterparty identifier (Clients.xlsx Counterparties)",
    "product_group": "One of EQUITY, BOND, FX, LISTED_DERIV, IRS, FINANCING", "product_type": "Asset-specific product type",
    "side": "BUY/SELL; PAY_FIXED/RECEIVE_FIXED for swaps; REPO/REVERSE_REPO/SEC_LOAN/SEC_BORROW for financing",
    "quantity": "Shares, nominal, contracts, base-currency amount or swap notional depending on product",
    "price": "Per-share price, clean % of par, FX rate, option premium, futures price, swap fixed rate % or repo dirty price %",
    "price_type": "How to read price", "trade_ccy": "Trade currency (ISO 4217)", "settle_ccy": "Settlement currency (ISO 4217)",
    "trade_date": "Trade date in the market's local time zone", "trade_time_utc": "Execution timestamp in UTC",
    "market": "Market / venue code (simulated)", "market_timezone": "IANA time zone of the market",
    "settlement_date": "Contractual settlement / value / effective / start date as booked by front office",
    "end_date": "Repo or loan end date, swap maturity", "source_system": "Upstream system that produced the row",
    "source_record_id": "Record identifier in the upstream system (lineage)", "ingest_batch": "Source batch identifier",
    "source_trade_ref": "Front-office external reference used for duplicate detection",
    "book": "Trading book", "trader_id": "Demo trader user", "fx_counter_ccy": "FX counter (quote) currency",
    "fx_counter_amount": "FX counter amount as booked", "fx_spot_rate": "Spot rate (forwards only)",
    "fx_forward_points": "Forward points in pips (forwards only)", "fx_tenor_months": "Forward tenor in months",
    "repo_rate_pct": "Repo rate, % per annum", "haircut_pct": "Collateral haircut %", "collateral_ccy": "Cash collateral currency",
    "sl_fee_bps": "Securities lending fee, bps per annum", "irs_fixed_rate_pct": "Swap fixed rate %",
    "irs_float_index": "Floating index (synthetic)", "irs_spread_bps": "Spread over floating index, bps",
    "irs_tenor_years": "Swap tenor in years", "execution_venue": "Venue or OTC",
}


def infer_type(values) -> str:
    seen = [v for v in values if v is not None]
    if not seen:
        return "STRING"
    if all(isinstance(v, datetime) for v in seen):
        return "TIMESTAMP"
    if all(isinstance(v, date) and not isinstance(v, datetime) for v in seen):
        return "DATE"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return "INTEGER"
    if all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in seen):
        return "DECIMAL"
    return "STRING"


def _cell(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def write_workbook(path: Path, sheets: dict[str, list[dict]], meta: dict, columns: dict[str, list[str]] | None = None,
                   descriptions: dict[str, str] | None = None) -> dict:
    """Write sheets; returns schema {sheet: [(col, type, nullable)]}."""
    wb = Workbook()
    wb.remove(wb.active)
    schema = {}
    desc = dict(DOCS, **(descriptions or {}))
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        cols = (columns or {}).get(name) or (list(rows[0].keys()) if rows else [])
        types = {c: infer_type([r.get(c) for r in rows]) for c in cols}
        schema[name] = [(c, types[c], any(r.get(c) is None for r in rows)) for c in cols]
        ws.append(cols)
        for r in rows:
            ws.append([_cell(r.get(c)) for c in cols])
        for j, c in enumerate(cols, start=1):
            h = ws.cell(row=1, column=j)
            h.fill, h.font = HEADER_FILL, HEADER_FONT
            h.alignment = Alignment(horizontal="center")
            fmt = TYPE_FORMAT[types[c]]
            if fmt != "@":
                for i in range(2, len(rows) + 2):
                    ws.cell(row=i, column=j).number_format = fmt
            width = max(len(c), *(len(str(r.get(c))) for r in rows[:200])) if rows else len(c)
            ws.column_dimensions[get_column_letter(j)].width = min(max(10, width + 2), 48)
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows) + 1}"
    d = wb.create_sheet("Dictionary")
    d.append(["sheet", "column", "type", "nullable", "excel_format", "description"])
    for name, cols in schema.items():
        for c, t, n in cols:
            d.append([name, c, t, "Y" if n else "N", TYPE_FORMAT[t], desc.get(c, c.replace("_", " ").capitalize())])
    p = wb.create_sheet("Provenance")
    for k, v in meta.items():
        p.append([k, v])
    p.append(["row_counts", ", ".join(f"{k}={len(v)}" for k, v in sheets.items())])
    for ws in (d, p):
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 28
        ws.column_dimensions["F"].width = 80
    wb.properties.created = FIXED_TS
    wb.properties.modified = FIXED_TS
    wb.properties.creator = "CapitalFlow 360 datagen"
    buf = io.BytesIO()
    wb.save(buf)
    path.write_bytes(_normalise_zip(buf.getvalue()))
    return schema


def _normalise_zip(data: bytes) -> bytes:
    """Rewrite the xlsx zip with fixed timestamps so identical content gives identical bytes."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-11-16T00:00:00Z", body)
            zi = zipfile.ZipInfo(info.filename, date_time=(2026, 11, 16, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(zi, body)
    return out.getvalue()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_workbook(path: Path) -> dict[str, list[dict]]:
    """Read data sheets back as typed python values (dates as date, ints as int)."""
    wb = load_workbook(path, data_only=True, read_only=True)
    dict_rows = list(wb["Dictionary"].iter_rows(values_only=True))[1:]
    types = {(r[0], r[1]): r[2] for r in dict_rows}
    out = {}
    for ws in wb.worksheets:
        if ws.title in ("Dictionary", "Provenance"):
            continue
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            out[ws.title] = []
            continue
        cols = rows[0]
        recs = []
        for r in rows[1:]:
            rec = {}
            for c, v in zip(cols, r, strict=False):
                t = types.get((ws.title, c))
                if v is not None and t == "DATE" and isinstance(v, datetime):
                    v = v.date()
                elif v is not None and t == "INTEGER":
                    v = int(v)
                elif v is not None and t == "DECIMAL" and isinstance(v, float):
                    v = float(repr(v))
                rec[c] = v
            recs.append(rec)
        out[ws.title] = recs
    return out
