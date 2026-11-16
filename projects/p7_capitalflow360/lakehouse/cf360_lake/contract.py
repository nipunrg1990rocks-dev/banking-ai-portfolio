"""Data contract: column names, types and nullability come from each baseline workbook's Dictionary sheet."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

TRACE_COLUMNS = {"negative_case_id"}  # allowed extra columns: test traceability ids, never business data
META_SHEETS = {"Dictionary", "Provenance"}


def table_name(sheet: str) -> str:
    return sheet.lower()


def build_contract(excel_dir: Path) -> dict[str, dict]:
    """{table: {"workbook": file, "sheet": name, "columns": [(name, type, nullable)]}}"""
    out: dict[str, dict] = {}
    for f in sorted(Path(excel_dir).glob("*.xlsx")):
        wb = load_workbook(f, read_only=True)
        rows = list(wb["Dictionary"].iter_rows(values_only=True))[1:]
        for sheet, col, typ, nullable, *_ in rows:
            t = out.setdefault(table_name(sheet), {"workbook": f.name, "sheet": sheet, "columns": []})
            t["columns"].append((col, typ, nullable == "Y"))
        wb.close()
    return out


def save(contract: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, indent=1))


def load(path: Path) -> dict:
    return {k: {**v, "columns": [tuple(c) for c in v["columns"]]} for k, v in json.loads(Path(path).read_text()).items()}
