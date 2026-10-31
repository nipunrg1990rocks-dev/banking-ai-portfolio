"""Shared constants for the Project 06 tests (a uniquely named module, so it never collides with other
projects' conftest.py files when the whole repository's suite runs in one pytest session)."""

from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
APAC_PROMPT = ("Create a six-slide APAC Q3 risk pack, compare Q2, rank top exception drivers, add commentary and "
               "speaker notes")
