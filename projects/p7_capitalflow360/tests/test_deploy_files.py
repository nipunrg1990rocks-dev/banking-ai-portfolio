"""The Vercel requirements file must pin exactly what the backend pins."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pins(p: Path) -> list[str]:
    return sorted(line.strip() for line in p.read_text().splitlines() if line.strip() and not line.startswith("#"))


def test_vercel_requirements_match_backend():
    assert pins(ROOT / "requirements.txt") == pins(ROOT / "backend" / "requirements.txt")
