"""Runtime configuration (environment variables; see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

KNOWN_DEFECTS = {
    "D1": "Allocation rounding residual is dropped (allocations no longer sum to the block amount)",
    "D2": "US corporate bond accrued interest uses ACT/360 instead of 30/360",
    "D3": "FX spot value date ignores the quote-currency holiday calendar",
    "D4": "Maker-checker allows the maker to approve their own request",
    "D5": "Replaying a settlement posts cash twice (idempotency bypass)",
    "D6": "Trade blotter ignores the currency filter",
}


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "CF360_DATABASE_URL", "postgresql+psycopg://cf360:cf360@localhost:5432/cf360"))
    jwt_secret: str = field(default_factory=lambda: os.getenv("CF360_JWT_SECRET", "dev-only-change-me"))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("CF360_JWT_TTL_MINUTES", "480")))
    demo_password: str = field(default_factory=lambda: os.getenv("CF360_DEMO_PASSWORD", "Demo!2026"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CF360_DATA_DIR", str(PROJECT_ROOT / "data"))))
    test_support: bool = field(default_factory=lambda: os.getenv("CF360_TEST_SUPPORT", "false").lower() == "true")
    defects: frozenset = field(default_factory=lambda: frozenset(
        d.strip() for d in os.getenv("CF360_DEFECTS", "").split(",") if d.strip() in KNOWN_DEFECTS))
    static_dir: Path | None = field(default_factory=lambda: Path(p) if (p := os.getenv("CF360_STATIC_DIR")) else None)


settings = Settings()


def defect(code: str) -> bool:
    """True only when a known defect was explicitly switched on (disabled by default)."""
    return code in settings.defects
