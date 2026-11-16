"""Shared shape of a reporting domain (dataset + KPIs + slide catalog + vocabulary)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..spec import DeckSpec, comparison_months, period_label
from ..store import Dataset, QueryBuilder, QueryRecord


@dataclass
class SlideType:
    key: str
    label: str
    keywords: list[str]  # regexes matched against prompt text
    description: str


@dataclass
class Template:
    key: str
    label: str
    slides: list[str]  # default order; first N are used
    keywords: list[str]
    accent: str = "#13315c"


@dataclass
class Context:
    spec: DeckSpec
    dataset: Dataset
    qb: QueryBuilder
    months: list[str]
    cmp_months: list[str]
    filters: dict[str, list[str]]
    queries: list[QueryRecord] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)

    @property
    def period(self) -> str:
        return period_label(self.months)

    @property
    def cmp_period(self) -> str:
        return period_label(self.cmp_months) if self.cmp_months else "no comparison"

    def filters_label(self, labels: dict[str, str] | None = None) -> str:
        if not self.filters:
            return "All data"
        parts = []
        for dim, vals in sorted(self.filters.items()):
            name = (labels or {}).get(dim, dim.replace("_", " ").title())
            parts.append(f"{name}: {', '.join(vals)}")
        return "; ".join(parts)

    def query(self, table: str, columns: list[str], filters: dict[str, list[str]] | None = None,
              months: list[str] | None = None, month_col: str = "reporting_month") -> pd.DataFrame:
        df, rec = self.qb.select(self.dataset, table, columns, filters, months, month_col)
        if rec.fingerprint not in {q.fingerprint for q in self.queries}:
            self.queries.append(rec)
        return df

    def memo(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


class Domain:
    key: str = ""
    label: str = ""
    prefix: str = ""
    org: str = "Ledgerline Bank (fictional)"
    currency: str = "₹"
    tables: dict[str, list[str]] = {}
    filter_dimensions: dict[str, str] = {}
    synonyms: list[tuple[str, str, str]] = []  # (regex, dimension, value)
    templates: dict[str, Template] = {}
    slide_catalog: dict[str, SlideType] = {}
    detect_keywords: list[str] = []
    examples: list[str] = []
    followup_examples: list[str] = []

    # ---- data
    def load(self, raw: pd.DataFrame, source: str, synthetic: bool, registry) -> Dataset:
        raise NotImplementedError

    def default_months(self, ds: Dataset) -> list[str]:
        """Latest complete calendar quarter in the dataset."""
        months = sorted(ds.months)
        for m in reversed(months):
            y, mo = map(int, m.split("-"))
            if mo % 3 == 0:
                q = [f"{y}-{mo - 2:02d}", f"{y}-{mo - 1:02d}", m]
                if all(x in months for x in q):
                    return q
        return months[-3:]

    def kpi_dictionary(self) -> pd.DataFrame:
        raise NotImplementedError

    # ---- analytics
    def context(self, spec: DeckSpec, ds: Dataset, qb: QueryBuilder) -> Context:
        ctx = Context(spec, ds, qb, sorted(spec.months), [], dict(spec.filters))
        cmp = comparison_months(spec)
        missing = [m for m in cmp if m not in ds.months]
        if cmp and missing:
            if len(missing) == len(cmp):
                ctx.disclosures.append(
                    f"Comparison period {period_label(cmp)} is not in dataset {ds.version} "
                    f"(data covers {period_label([ds.months[0]])}–{period_label([ds.months[-1]])}); "
                    "comparisons are shown as n/a.")
                cmp = []
            else:
                ctx.disclosures.append(f"Comparison period is partial: {', '.join(missing)} missing.")
                cmp = [m for m in cmp if m in ds.months]
        ctx.cmp_months = cmp
        if ds.report.quarantined:
            ctx.disclosures.append(f"{ds.report.quarantined} source rows were quarantined at validation and are "
                                   "excluded from every figure.")
        for w in ds.report.warnings:
            ctx.disclosures.append(f"Data warning: {w.name} ({w.count}). {w.detail}")
        return ctx

    def build_slide(self, slide_type: str, ctx: Context, no: int, total: int) -> dict[str, Any]:
        raise NotImplementedError

    def deck_title(self, spec: DeckSpec) -> str:
        return spec.title

    def filter_value_for(self, text: str) -> list[tuple[str, str]]:
        found = []
        for pattern, dim, value in self.synonyms:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                if value == "{1}":
                    value = m.group(1).upper()
                found.append((dim, value))
        return found

    def display(self, value: str) -> str:
        return value

    def expand(self, dim: str, value: str) -> list[str]:
        """Business shorthand, e.g. 'high severity' means High and Critical."""
        return [value]
