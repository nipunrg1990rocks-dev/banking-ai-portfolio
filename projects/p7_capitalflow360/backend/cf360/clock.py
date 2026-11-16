"""Wall-clock helper: naive UTC timestamps (all DateTime columns store UTC without tzinfo)."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
