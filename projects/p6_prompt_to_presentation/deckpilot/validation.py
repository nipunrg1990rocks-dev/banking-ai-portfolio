"""Validation gate between an uploaded/refreshed file and the governed store.

Three outcomes per check:

* ``error``   - the file is rejected (wrong columns, unreadable, no usable rows)
* ``quarantine`` - offending rows are removed and kept aside with a reason
* ``warning`` - data is accepted but the issue is disclosed (and shown on slides)

A dataset can only become "active" (feed the dashboard and new decks) when it has no
errors. Nothing in this module executes content from the file: cells are only read as
values and compared against allowlists and ranges.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 50_000
FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass
class Check:
    name: str
    level: str  # ok | warning | quarantine | error
    count: int
    detail: str


@dataclass
class ValidationReport:
    source: str
    rows_in: int = 0
    rows_accepted: int = 0
    checks: list[Check] = field(default_factory=list)
    quarantine: pd.DataFrame | None = None

    def add(self, name: str, level: str, count: int, detail: str) -> None:
        self.checks.append(Check(name, level if count or level == "error" else "ok", int(count), detail))

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warning"]

    @property
    def quarantined(self) -> int:
        return 0 if self.quarantine is None else len(self.quarantine)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame([c.__dict__ for c in self.checks])

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "rows_in": self.rows_in,
            "rows_accepted": self.rows_accepted,
            "rows_quarantined": self.quarantined,
            "errors": [c.__dict__ for c in self.errors],
            "warnings": [c.__dict__ for c in self.warnings],
        }


def read_table(data: bytes, filename: str, sheet_hint: str | None = None) -> pd.DataFrame:
    """Read CSV or XLSX bytes into strings-only cells (no formula evaluation, no macros)."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is {len(data) / 1e6:.1f} MB; the limit is {MAX_UPLOAD_BYTES / 1e6:.0f} MB")
    name = filename.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, na_values=[""])
    elif name.endswith((".xlsx", ".xlsm")):
        # openpyxl reads cached values only; VBA in .xlsm is never loaded or run.
        xl = pd.ExcelFile(io.BytesIO(data), engine="openpyxl")
        sheet = sheet_hint if sheet_hint in xl.sheet_names else xl.sheet_names[0]
        df = xl.parse(sheet, dtype=object)
    else:
        raise ValueError("only .csv, .xlsx or .xlsm files are accepted")
    if len(df) > MAX_ROWS:
        raise ValueError(f"{len(df)} rows exceeds the {MAX_ROWS} row limit")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def strip_formula_text(series: pd.Series) -> tuple[pd.Series, int]:
    """Neutralise spreadsheet-formula-looking text so it can never be re-exported as a live formula."""
    s = series.astype("string")
    mask = s.str.startswith(FORMULA_PREFIXES, na=False) & ~s.str.fullmatch(r"[-+]?\d+(\.\d+)?", na=False)
    return s.where(~mask, "'" + s), int(mask.sum())


def check_columns(df: pd.DataFrame, required: list[str], allowed: list[str], report: ValidationReport,
                  ignore_extra: bool = False) -> bool:
    missing = [c for c in required if c not in df.columns]
    report.add("Required columns present", "error" if missing else "ok", len(missing),
               "missing: " + ", ".join(missing) if missing else "all required columns found")
    extra = [c for c in df.columns if c not in allowed and not c.startswith("Unnamed")]
    if extra and not ignore_extra:
        report.add("Unexpected columns", "warning", len(extra), "ignored (not in data contract): " + ", ".join(extra))
    return not missing


def quarantine_rows(df: pd.DataFrame, mask: pd.Series, reason: str, bucket: list[pd.DataFrame]) -> pd.DataFrame:
    if mask.any():
        bad = df[mask].copy()
        bad["quarantine_reason"] = reason
        bucket.append(bad)
    return df[~mask]
