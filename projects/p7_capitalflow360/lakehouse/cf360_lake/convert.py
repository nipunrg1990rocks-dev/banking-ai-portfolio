"""Validated Excel -> Parquet conversion (the landing zone).

Primary ingestion path on every environment: Databricks reads Excel natively only on DBR 17.1+,
so converting with openpyxl first keeps Free Edition / serverless and local runs identical.
Every value is landed as text (Bronze keeps what was received); Silver casts using the contract.
A sheet whose columns differ from the contract is rejected whole (DQ05 schema drift).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import load_workbook

from cf360_lake.contract import META_SHEETS, TRACE_COLUMNS, table_name


def _text(v, typ: str = "STRING") -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if typ == "TIMESTAMP" else v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(int(v)) if v.is_integer() else repr(v)
    return str(v)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def convert(files: list[Path], landing: Path, batch_id: str, contract: dict) -> dict:
    """Land every data sheet of ``files`` as landing/<table>/batch_id=<id>/part-0.parquet."""
    manifest = {"batch_id": batch_id, "converted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": [],
                "tables": {}, "rejected": []}
    for f in files:
        wb = load_workbook(f, read_only=True, data_only=True)
        manifest["files"].append({"file": Path(f).name, "sha256": sha256(Path(f))})
        for ws in wb.worksheets:
            if ws.title in META_SHEETS:
                continue
            t = table_name(ws.title)
            rows = list(ws.iter_rows(values_only=True))
            header = [str(h) for h in rows[0]] if rows else []
            spec = contract.get(t)
            if spec is None:
                manifest["rejected"].append({"file": Path(f).name, "sheet": ws.title, "rule_id": "DQ05", "error": "E_SCHEMA_DRIFT",
                                             "reason": "sheet not in the data contract"})
                continue
            expected = [c[0] for c in spec["columns"]]
            missing = [c for c in expected if c not in header]
            unexpected = [c for c in header if c not in expected and c not in TRACE_COLUMNS]
            if missing or unexpected:
                manifest["rejected"].append({"file": Path(f).name, "sheet": ws.title, "rule_id": "DQ05", "error": "E_SCHEMA_DRIFT",
                                             "missing": missing, "unexpected": unexpected, "rows": len(rows) - 1})
                continue
            cols = expected + [c for c in header if c in TRACE_COLUMNS]
            idx = {h: i for i, h in enumerate(header)}
            types = {c[0]: c[1] for c in spec["columns"]}
            data = {c: [_text(r[idx[c]], types.get(c, "STRING")) for r in rows[1:]] for c in cols}
            out = Path(landing) / t / f"batch_id={batch_id}"
            out.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.table({c: pa.array(v, type=pa.string()) for c, v in data.items()}), out / "part-0.parquet")
            manifest["tables"][t] = {"rows": len(rows) - 1, "file": Path(f).name, "sheet": ws.title}
        wb.close()
    (Path(landing) / "_batches").mkdir(parents=True, exist_ok=True)
    (Path(landing) / "_batches" / f"{batch_id}.json").write_text(json.dumps(manifest, indent=1))
    return manifest
