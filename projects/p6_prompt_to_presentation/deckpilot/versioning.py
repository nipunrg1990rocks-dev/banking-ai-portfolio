"""Deck version history, review comments and approvals (SQLite).

Versions are append-only: every generation, follow-up, refresh, commentary edit or copy
inserts a new row and a new file; nothing is updated in place except a version's review
status. Each row records the prompt, parser mode, validated spec, dataset version + hash,
filters, period, query fingerprints and the full computed payload, so any version can be
re-rendered or audited later.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REVIEW_STATES = ["draft", "in_review", "changes_requested", "approved"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VersionRecord:
    version_id: str
    deck_id: str
    version_no: int
    parent_id: str | None
    created_at: str
    actor: str
    action: str
    prompt: str
    parser_mode: str
    spec: dict[str, Any]
    dataset_version: str
    dataset_sha: str
    filters: dict[str, Any]
    period: str
    payload: dict[str, Any]
    file_path: str
    file_sha256: str
    review_status: str
    notes: list[str]
    fingerprints: list[str]
    approved_file: str | None

    @property
    def label(self) -> str:
        return f"{self.deck_id} v{self.version_no}"


COLS = ["version_id", "deck_id", "version_no", "parent_id", "created_at", "actor", "action", "prompt", "parser_mode",
        "spec_json", "dataset_version", "dataset_sha", "filters_json", "period", "payload_json", "file_path",
        "file_sha256", "review_status", "notes_json", "fingerprints_json", "approved_file"]


class VersionStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS decks (deck_id TEXT PRIMARY KEY, domain TEXT, title TEXT, created_at TEXT,
                                              created_by TEXT);
            CREATE TABLE IF NOT EXISTS versions (version_id TEXT PRIMARY KEY, deck_id TEXT, version_no INTEGER,
                parent_id TEXT, created_at TEXT, actor TEXT, action TEXT, prompt TEXT, parser_mode TEXT,
                spec_json TEXT, dataset_version TEXT, dataset_sha TEXT, filters_json TEXT, period TEXT,
                payload_json TEXT, file_path TEXT, file_sha256 TEXT, review_status TEXT, notes_json TEXT,
                fingerprints_json TEXT, approved_file TEXT, UNIQUE(deck_id, version_no));
            CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT,
                slide_no INTEGER, author TEXT, text TEXT, status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT,
                reviewer TEXT, decision TEXT, note TEXT, created_at TEXT);
        """)
        self.con.commit()

    # ---------------------------------------------------------------- decks + versions
    def new_deck(self, domain: str, title: str, actor: str) -> str:
        with self._lock:
            n = self.con.execute("SELECT COUNT(*) FROM decks").fetchone()[0] + 1
            deck_id = f"D-{n:04d}"
            self.con.execute("INSERT INTO decks VALUES (?,?,?,?,?)", (deck_id, domain, title, now(), actor))
            self.con.commit()
        return deck_id

    def next_version_no(self, deck_id: str) -> int:
        row = self.con.execute("SELECT MAX(version_no) FROM versions WHERE deck_id=?", (deck_id,)).fetchone()
        return (row[0] or 0) + 1

    def add_version(self, rec: VersionRecord) -> VersionRecord:
        values = [rec.version_id, rec.deck_id, rec.version_no, rec.parent_id, rec.created_at, rec.actor, rec.action,
                  rec.prompt, rec.parser_mode, json.dumps(rec.spec), rec.dataset_version, rec.dataset_sha,
                  json.dumps(rec.filters), rec.period, json.dumps(rec.payload, default=str), rec.file_path,
                  rec.file_sha256, rec.review_status, json.dumps(rec.notes), json.dumps(rec.fingerprints),
                  rec.approved_file]
        with self._lock:
            self.con.execute(f"INSERT INTO versions ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})", values)
            self.con.execute("UPDATE decks SET title=? WHERE deck_id=?", (rec.payload["slides"][0]["title"],
                                                                          rec.deck_id))
            self.con.commit()
        return rec

    def _row(self, r) -> VersionRecord:
        d = dict(zip(COLS, r, strict=True))
        return VersionRecord(
            d["version_id"], d["deck_id"], d["version_no"], d["parent_id"], d["created_at"], d["actor"], d["action"],
            d["prompt"], d["parser_mode"], json.loads(d["spec_json"]), d["dataset_version"], d["dataset_sha"],
            json.loads(d["filters_json"]), d["period"], json.loads(d["payload_json"]), d["file_path"],
            d["file_sha256"], d["review_status"], json.loads(d["notes_json"]), json.loads(d["fingerprints_json"]),
            d["approved_file"])

    def get(self, version_id: str) -> VersionRecord:
        r = self.con.execute(f"SELECT {','.join(COLS)} FROM versions WHERE version_id=?", (version_id,)).fetchone()
        if not r:
            raise KeyError(version_id)
        return self._row(r)

    def versions(self, deck_id: str | None = None) -> list[VersionRecord]:
        sql = f"SELECT {','.join(COLS)} FROM versions"
        params: tuple = ()
        if deck_id:
            sql += " WHERE deck_id=?"
            params = (deck_id,)
        return [self._row(r) for r in self.con.execute(sql + " ORDER BY created_at, deck_id, version_no", params)]

    def decks(self) -> list[dict[str, Any]]:
        rows = self.con.execute("SELECT d.deck_id, d.domain, d.title, d.created_at, COUNT(v.version_id) FROM decks d "
                                "LEFT JOIN versions v ON v.deck_id = d.deck_id GROUP BY d.deck_id ORDER BY d.deck_id")
        return [dict(zip(["deck_id", "domain", "title", "created_at", "versions"], r, strict=True)) for r in rows]

    def set_status(self, version_id: str, status: str, approved_file: str | None = None) -> None:
        if status not in REVIEW_STATES:
            raise ValueError(status)
        with self._lock:
            self.con.execute("UPDATE versions SET review_status=?, approved_file=COALESCE(?, approved_file) "
                             "WHERE version_id=?", (status, approved_file, version_id))
            self.con.commit()

    # ---------------------------------------------------------------- review panel
    def add_comment(self, version_id: str, slide_no: int, author: str, text: str) -> int:
        with self._lock:
            cur = self.con.execute("INSERT INTO comments (version_id, slide_no, author, text, status, created_at) "
                                   "VALUES (?,?,?,?,?,?)", (version_id, slide_no, author, text, "open", now()))
            self.con.commit()
            return int(cur.lastrowid)

    def resolve_comment(self, comment_id: int) -> None:
        with self._lock:
            self.con.execute("UPDATE comments SET status='resolved' WHERE id=?", (comment_id,))
            self.con.commit()

    def comments(self, version_id: str) -> list[dict[str, Any]]:
        rows = self.con.execute("SELECT id, version_id, slide_no, author, text, status, created_at FROM comments "
                                "WHERE version_id=? ORDER BY id", (version_id,))
        return [dict(zip(["id", "version_id", "slide_no", "author", "text", "status", "created_at"], r, strict=True))
                for r in rows]

    def add_review(self, version_id: str, reviewer: str, decision: str, note: str) -> None:
        with self._lock:
            self.con.execute("INSERT INTO reviews (version_id, reviewer, decision, note, created_at) VALUES "
                             "(?,?,?,?,?)", (version_id, reviewer, decision, note, now()))
            self.con.commit()

    def reviews(self, version_id: str) -> list[dict[str, Any]]:
        rows = self.con.execute("SELECT reviewer, decision, note, created_at FROM reviews WHERE version_id=? ORDER BY "
                                "id", (version_id,))
        return [dict(zip(["reviewer", "decision", "note", "created_at"], r, strict=True)) for r in rows]

    def review_log_csv(self, deck_id: str) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["deck_id", "version", "created_at", "action", "actor", "prompt", "dataset_version", "period",
                    "filters", "review_status", "record_type", "slide_no", "author", "text", "status", "at"])
        for v in self.versions(deck_id):
            base = [v.deck_id, v.version_no, v.created_at, v.action, v.actor, v.prompt, v.dataset_version, v.period,
                    json.dumps(v.filters), v.review_status]
            w.writerow(base + ["version", "", v.actor, "; ".join(v.notes), "", v.created_at])
            for c in self.comments(v.version_id):
                w.writerow(base + ["comment", c["slide_no"], c["author"], c["text"], c["status"], c["created_at"]])
            for r in self.reviews(v.version_id):
                w.writerow(base + ["review", "", r["reviewer"], r["note"], r["decision"], r["created_at"]])
        return buf.getvalue()
