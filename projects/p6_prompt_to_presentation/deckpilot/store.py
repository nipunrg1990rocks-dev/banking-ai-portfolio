"""Governed dataset store (DuckDB) and allowlisted SQL query builder.

* Each validated upload becomes an immutable ``Dataset`` version. Older versions stay
  queryable so an earlier deck can be regenerated exactly.
* The only SQL that ever runs is produced by ``QueryBuilder.select``: table and column
  names come from the domain's allowlist, every filter value is a bound parameter, and
  there is no path for user text to become SQL.
* Every query returns a fingerprint (SHA-256 of SQL + parameters + dataset version) that
  is written into the deck's provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from .validation import ValidationReport


@dataclass
class Dataset:
    domain: str
    version: str
    tables: dict[str, pd.DataFrame]
    months: list[str]
    report: ValidationReport
    source: str
    synthetic: bool
    sha256: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    dim_table: str = "fact"
    snapshot: str | None = None  # as-of date of the source extract (YYYY-MM-DD), if known
    notes: list[str] = field(default_factory=list)

    @property
    def as_of(self) -> str:
        return self.months[-1] if self.months else "n/a"

    def dimension_values(self, dim: str) -> list[str]:
        df = self.tables[self.dim_table]
        if dim not in df.columns:
            return []
        return sorted(str(v) for v in df[dim].dropna().unique())

    def meta(self) -> dict[str, Any]:
        return {
            "domain": self.domain, "version": self.version, "source": self.source, "synthetic": self.synthetic,
            "sha256": self.sha256, "created_at": self.created_at, "months": [self.months[0], self.months[-1]]
            if self.months else [], "rows": {k: len(v) for k, v in self.tables.items()},
            "quarantined": self.report.quarantined, "warnings": len(self.report.warnings), "snapshot": self.snapshot,
        }


def content_hash(tables: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for name in sorted(tables):
        h.update(name.encode())
        h.update(pd.util.hash_pandas_object(tables[name], index=False).values.tobytes())
    return h.hexdigest()


@dataclass
class QueryRecord:
    table: str
    sql: str
    params: list[Any]
    dataset_version: str
    rows: int
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {"table": self.table, "sql": self.sql, "params": self.params, "dataset_version": self.dataset_version,
                "rows": self.rows, "fingerprint": self.fingerprint}


IDENT = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class QueryBuilder:
    """Builds and runs parameterised SELECTs over allowlisted tables and columns only."""

    def __init__(self, allowlist: dict[str, list[str]]) -> None:
        self.allowlist = allowlist
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self._registered: set[str] = set()

    def _view(self, ds: Dataset, table: str) -> str:
        slug = re.sub(r"[^a-z0-9]", "_", ds.version.lower())
        view = f"{table}__{slug}"
        if view not in self._registered:
            self._con.register(view, ds.tables[table])
            self._registered.add(view)
        return view

    def select(self, ds: Dataset, table: str, columns: list[str], filters: dict[str, list[str]] | None = None,
               months: list[str] | None = None, month_col: str = "reporting_month") -> tuple[pd.DataFrame, QueryRecord]:
        if table not in self.allowlist:
            raise PermissionError(f"table {table!r} is not approved")
        allowed = self.allowlist[table]
        for c in columns:
            if c not in allowed or not IDENT.match(c):
                raise PermissionError(f"column {c!r} is not approved for {table}")
        where, params = [], []
        for dim, values in sorted((filters or {}).items()):
            if dim not in allowed or not IDENT.match(dim):
                raise PermissionError(f"filter column {dim!r} is not approved for {table}")
            if values:
                where.append(f'"{dim}" IN ({", ".join("?" for _ in values)})')
                params.extend(str(v) for v in values)
        if months is not None:
            if month_col not in allowed:
                raise PermissionError("month column not approved")
            if not months:
                where.append("1 = 0")
            else:
                where.append(f'"{month_col}" IN ({", ".join("?" for _ in months)})')
                params.extend(months)
        cols = ", ".join(f'"{c}"' for c in columns)
        logical_sql = f"SELECT {cols} FROM {table}" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self._lock:
            view = self._view(ds, table)
            physical_sql = logical_sql.replace(f"FROM {table}", f"FROM {view}", 1)
            df = self._con.execute(physical_sql, params).df()
        fp = hashlib.sha256(json.dumps([logical_sql, params, ds.version]).encode()).hexdigest()[:16]
        return df, QueryRecord(table, logical_sql, params, ds.version, len(df), fp)


class DatasetRegistry:
    """Holds every validated dataset version per domain plus which one is active."""

    def __init__(self) -> None:
        self.versions: dict[str, Dataset] = {}
        self.active: dict[str, str] = {}
        self.staged: dict[str, Dataset] = {}

    def next_version(self, domain_prefix: str, sha: str) -> str:
        n = sum(1 for v in self.versions.values() if v.version.startswith(domain_prefix)) + 1
        n += sum(1 for v in self.staged.values() if v.version.startswith(domain_prefix))
        return f"{domain_prefix}-v{n}-{sha[:6]}"

    def stage(self, ds: Dataset) -> None:
        self.staged[ds.version] = ds

    def activate(self, version: str) -> Dataset:
        ds = self.staged.pop(version, None) or self.versions.get(version)
        if ds is None:
            raise KeyError(version)
        if not ds.report.ok:
            raise ValueError("dataset failed validation and cannot be activated")
        self.versions[ds.version] = ds
        self.active[ds.domain] = ds.version
        return ds

    def get(self, version: str) -> Dataset:
        return self.versions[version]

    def current(self, domain: str) -> Dataset:
        return self.versions[self.active[domain]]

    def history(self, domain: str) -> list[Dataset]:
        return [d for d in self.versions.values() if d.domain == domain]
