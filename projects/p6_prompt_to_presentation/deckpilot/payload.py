"""Slide payload: the JSON contract between analytics and the PowerPoint renderer.

A payload holds every number that appears on a slide, so a deck version can be
re-rendered byte-for-byte later (e.g. after approval) without re-querying data, and
every commentary sentence carries the evidence (metric, value, window, filters) it
was generated from.
"""

from __future__ import annotations

import math
import re
from typing import Any


def safe_div(num: float | None, den: float | None) -> float | None:
    """Ratio with a divide-by-zero / missing-data guard: returns None instead of inf or NaN."""
    if num is None or den is None:
        return None
    try:
        if den == 0 or math.isnan(den) or math.isnan(num):
            return None
    except TypeError:
        return None
    return num / den


def fmt_pct(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{dp}f}%"


def fmt_pp(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:+.{dp}f} pp"


def fmt_int(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def fmt_num(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{dp}f}"


def fmt_money(v: float | None, symbol: str = "₹") -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if symbol == "₹":
        if a >= 1e7:
            return f"{sign}₹{a / 1e7:,.2f} cr"
        if a >= 1e5:
            return f"{sign}₹{a / 1e5:,.1f} lakh"
        return f"{sign}₹{a:,.0f}"
    if a >= 1e6:
        return f"{sign}{symbol}{a / 1e6:,.2f}m"
    if a >= 1e3:
        return f"{sign}{symbol}{a / 1e3:,.0f}k"
    return f"{sign}{symbol}{a:,.0f}"


def change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None:
        return None
    return cur - prev


def pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev)


def rag(delta: float | None, higher_is_worse: bool, tolerance: float = 0.0) -> str:
    if delta is None:
        return "grey"
    if abs(delta) <= tolerance:
        return "amber"
    worse = delta > 0 if higher_is_worse else delta < 0
    return "red" if worse else "green"


# ------------------------------------------------------------------ blocks
def kpis(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "kpis", "items": items}


def kpi(label: str, value: str, sub: str = "", tone: str = "grey") -> dict[str, Any]:
    return {"label": label, "value": value, "sub": sub, "tone": tone}


def chart(kind: str, title: str, categories: list[str], series: list[dict[str, Any]],
          number_format: str = "#,##0", **extra: Any) -> dict[str, Any]:
    clean = []
    for s in series:
        vals = [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v) for v in s["values"]]
        clean.append({"name": s["name"], "values": vals})
    return {"kind": "chart", "chart": kind, "title": title, "categories": [str(c) for c in categories],
            "series": clean, "number_format": number_format, **extra}


def table(title: str, columns: list[str], rows: list[list[Any]], heat: dict[str, Any] | None = None,
          col_widths: list[float] | None = None) -> dict[str, Any]:
    return {"kind": "table", "title": title, "columns": columns, "rows": [[_cell(c) for c in r] for r in rows],
            "heat": heat, "col_widths": col_widths}


def cards(items: list[dict[str, str]], numbered: bool = False) -> dict[str, Any]:
    return {"kind": "cards", "items": items, "numbered": numbered}


def bullets(title: str, items: list[str]) -> dict[str, Any]:
    return {"kind": "bullets", "title": title, "items": items}


def _cell(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def fact(text: str, metric: str, value: Any, window: str, filters: str, kind: str = "fact") -> dict[str, Any]:
    """A commentary sentence plus the evidence that grounds it."""
    return {"text": text, "kind": kind, "evidence": {"metric": metric, "value": _cell(value), "window": window,
                                                     "filters": filters}}


def slide(slide_type: str, title: str, subtitle: str, layout: str, blocks: list[dict[str, Any]],
          commentary: list[dict[str, Any]], footnote: str, notes_extra: str = "") -> dict[str, Any]:
    return {"slide_type": slide_type, "title": title, "subtitle": subtitle, "layout": layout, "blocks": blocks,
            "commentary": commentary, "footnote": footnote, "notes_extra": notes_extra, "human_edited": False}


NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(text or "") if any(ch.isdigit() for ch in n)}


def unverified_numbers(edited: str, slide_payload: dict[str, Any]) -> list[str]:
    """Numbers in human-edited commentary that do not appear anywhere in the slide's computed payload."""
    import json

    known = numbers_in(json.dumps(slide_payload, default=str, ensure_ascii=False))
    for c in slide_payload.get("commentary", []):
        known |= numbers_in(c.get("text", ""))
    # Accept trivial numbers (1-digit list markers, years) without evidence.
    return sorted(n for n in numbers_in(edited) if n not in known and not re.fullmatch(r"\d|20\d\d", n))
