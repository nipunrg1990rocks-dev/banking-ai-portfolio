"""Tamper-evident audit trail shared by all portfolio projects.

Every event is appended to a SQLite table. Each row stores the SHA-256 hash of
its own content plus the hash of the previous row, forming a hash chain. If any
historical row is edited or deleted, `verify_chain()` reports where the chain
breaks. This mirrors the "immutable audit log" expectation in banking controls
without needing a dedicated ledger database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    timestamp: str
    actor: str
    event_type: str
    correlation_id: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


def _compute_hash(prev_hash: str, timestamp: str, actor: str, event_type: str,
                  correlation_id: str, payload_json: str) -> str:
    material = "|".join([prev_hash, timestamp, actor, event_type, correlation_id, payload_json])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only, hash-chained audit log backed by SQLite."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_events (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   timestamp TEXT NOT NULL,
                   actor TEXT NOT NULL,
                   event_type TEXT NOT NULL,
                   correlation_id TEXT NOT NULL,
                   payload TEXT NOT NULL,
                   prev_hash TEXT NOT NULL,
                   hash TEXT NOT NULL
               )"""
        )
        self._conn.commit()

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def record(self, event_type: str, payload: dict[str, Any], *, actor: str = "system",
               correlation_id: str = "-") -> AuditEvent:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        with self._lock:
            prev_hash = self._last_hash()
            digest = _compute_hash(prev_hash, timestamp, actor, event_type, correlation_id, payload_json)
            cur = self._conn.execute(
                "INSERT INTO audit_events (timestamp, actor, event_type, correlation_id, payload, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (timestamp, actor, event_type, correlation_id, payload_json, prev_hash, digest),
            )
            self._conn.commit()
            seq = cur.lastrowid
        return AuditEvent(seq, timestamp, actor, event_type, correlation_id,
                          json.loads(payload_json), prev_hash, digest)

    def events(self, correlation_id: str | None = None, limit: int | None = None) -> list[AuditEvent]:
        sql = "SELECT seq, timestamp, actor, event_type, correlation_id, payload, prev_hash, hash FROM audit_events"
        params: list[Any] = []
        if correlation_id:
            sql += " WHERE correlation_id = ?"
            params.append(correlation_id)
        sql += " ORDER BY seq DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [AuditEvent(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7]) for r in rows]

    def verify_chain(self) -> tuple[bool, int | None]:
        """Recompute every hash. Returns (ok, first_broken_seq)."""
        prev = GENESIS_HASH
        rows = self._conn.execute(
            "SELECT seq, timestamp, actor, event_type, correlation_id, payload, prev_hash, hash"
            " FROM audit_events ORDER BY seq ASC"
        ).fetchall()
        for seq, ts, actor, etype, cid, payload_json, prev_hash, digest in rows:
            if prev_hash != prev:
                return False, seq
            if _compute_hash(prev_hash, ts, actor, etype, cid, payload_json) != digest:
                return False, seq
            prev = digest
        return True, None

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
