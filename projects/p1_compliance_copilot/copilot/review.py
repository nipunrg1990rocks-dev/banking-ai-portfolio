"""Human review queue for answers that need a compliance officer's decision."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISIONS = ("Approved", "Approved with amendment", "Rejected")


@dataclass
class ReviewItem:
    item_id: int
    query_id: str
    created_at: str
    asked_by: str
    question: str
    answer: str
    confidence: str
    flags: list[str]
    sources: list[str]
    status: str
    reviewer: str | None
    comment: str | None
    final_answer: str | None
    decided_at: str | None


class ReviewQueue:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS review_items (
                   item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                   query_id TEXT NOT NULL UNIQUE,
                   created_at TEXT NOT NULL,
                   asked_by TEXT NOT NULL,
                   question TEXT NOT NULL,
                   answer TEXT NOT NULL,
                   confidence TEXT NOT NULL,
                   flags TEXT NOT NULL,
                   sources TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'Pending',
                   reviewer TEXT, comment TEXT, final_answer TEXT, decided_at TEXT
               )"""
        )
        self._conn.commit()

    def submit(self, *, query_id: str, asked_by: str, question: str, answer: str,
               confidence: str, flags: list[str], sources: list[str]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO review_items"
                " (query_id, created_at, asked_by, question, answer, confidence, flags, sources)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (query_id, now, asked_by, question, answer, confidence, json.dumps(flags), json.dumps(sources)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def _row(self, r: tuple[Any, ...]) -> ReviewItem:
        return ReviewItem(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]), json.loads(r[8]),
                          r[9], r[10], r[11], r[12], r[13])

    def items(self, status: str | None = None) -> list[ReviewItem]:
        sql = "SELECT * FROM review_items"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        return [self._row(r) for r in self._conn.execute(sql + " ORDER BY item_id DESC", params).fetchall()]

    def get(self, item_id: int) -> ReviewItem | None:
        r = self._conn.execute("SELECT * FROM review_items WHERE item_id = ?", (item_id,)).fetchone()
        return self._row(r) if r else None

    def decide(self, item_id: int, *, decision: str, reviewer: str, comment: str,
               amended_answer: str | None = None) -> ReviewItem:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        if not reviewer.strip():
            raise ValueError("reviewer name is required")
        if not comment.strip():
            raise ValueError("a rationale comment is required for every review decision")
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        if item.status != "Pending":
            raise ValueError(f"item {item_id} was already decided ({item.status})")
        if reviewer.strip().lower() == item.asked_by.strip().lower():
            raise ValueError("maker-checker control: the person who asked cannot review their own answer")
        if decision == "Approved with amendment" and not (amended_answer or "").strip():
            raise ValueError("an amended answer is required for 'Approved with amendment'")
        final = None if decision == "Rejected" else (amended_answer or item.answer)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "UPDATE review_items SET status=?, reviewer=?, comment=?, final_answer=?, decided_at=? WHERE item_id=?",
                (decision, reviewer.strip(), comment.strip(), final, now, item_id),
            )
            self._conn.commit()
        return self.get(item_id)  # type: ignore[return-value]

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) FROM review_items GROUP BY status").fetchall()
        return {status: n for status, n in rows}
