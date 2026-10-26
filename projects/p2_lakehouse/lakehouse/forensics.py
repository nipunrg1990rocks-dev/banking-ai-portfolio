"""File-level evidence about a delivery in the landing zone, compared with its manifest.

Pure Python (no Spark), so it works on a laptop folder, a Unity Catalog volume
(mounted under ``/Volumes``) and in the app snapshot. The Data Steward agent
calls it as a tool when a Bronze control blocks a batch.
"""

from __future__ import annotations

import json
from pathlib import Path

from .synthetic import file_fingerprint

TAIL_CHARS = 80


def _line_stats(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    last = lines[-1] if lines else ""
    stats = {"lines": len(lines), "ends_with_newline": text.endswith("\n"), "last_line_tail": last[-TAIL_CHARS:]}
    if path.suffix == ".jsonl":
        try:
            json.loads(last)
            stats["last_line_is_valid_json"] = True
        except ValueError:
            stats["last_line_is_valid_json"] = False
    else:
        stats["data_rows"] = max(len(lines) - 1, 0)  # minus the CSV header
    return stats


def inspect_delivery(landing: str | Path, batch_id: str) -> dict:
    """Compare every file in ``batch=<id>`` with the size and SHA-256 its manifest promised."""
    folder = Path(landing) / f"batch={batch_id}"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = {}
    for name, promised in manifest.get("files", {}).items():
        path = folder / name
        if not path.exists():
            files[name] = {"present": False, "expected_bytes": promised["bytes"]}
            continue
        actual = file_fingerprint(path)
        files[name] = {
            "present": True,
            "expected_bytes": promised["bytes"],
            "actual_bytes": actual["bytes"],
            "checksum_matches": actual["sha256"] == promised["sha256"],
            **_line_stats(path),
        }
    return {"batch_id": batch_id, "source_system": manifest["source_system"],
            "business_date": manifest["business_date"], "manifest_record_counts": manifest["record_counts"],
            "manifest_control_total": manifest["transactions_control_total"], "files": files}
