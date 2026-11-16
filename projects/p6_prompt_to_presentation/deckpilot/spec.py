"""Validated presentation specification.

Whatever produces a spec (the rules parser, Claude, or the dashboard's "deck from
this view" button) must produce one of these. Pydantic enforces the shape and
`DeckSpec.validate_against(domain, dataset)` enforces the governed vocabulary: only
known slide types, only allowlisted filter dimensions, only values that exist in the
validated dataset, only months the dataset covers.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MIN_SLIDES, MAX_SLIDES = 3, 12


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "prior_period", "yoy", "custom"] = "prior_period"
    months: list[str] = Field(default_factory=list)  # only used when mode == "custom"

    @field_validator("months")
    @classmethod
    def _months(cls, v: list[str]) -> list[str]:
        for m in v:
            if not MONTH_RE.match(m):
                raise ValueError(f"month must be YYYY-MM, got {m!r}")
        return sorted(set(v))


class SlideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slide_type: str = Field(pattern=r"^[a-z_]{2,40}$")
    title: str | None = Field(default=None, max_length=120)


class DeckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(pattern=r"^[a-z_]{2,30}$")
    template: str = Field(pattern=r"^[a-z_]{2,40}$")
    title: str = Field(max_length=120)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    months: list[str] = Field(default_factory=list)
    comparison: Comparison = Field(default_factory=Comparison)
    slides: list[SlideRequest]
    top_n: int = Field(default=5, ge=3, le=10)
    speaker_notes: bool = True
    commentary: bool = True

    @field_validator("months")
    @classmethod
    def _months(cls, v: list[str]) -> list[str]:
        for m in v:
            if not MONTH_RE.match(m):
                raise ValueError(f"month must be YYYY-MM, got {m!r}")
        return sorted(set(v))

    @field_validator("slides")
    @classmethod
    def _slides(cls, v: list[SlideRequest]) -> list[SlideRequest]:
        if not MIN_SLIDES <= len(v) <= MAX_SLIDES:
            raise ValueError(f"a deck needs {MIN_SLIDES}-{MAX_SLIDES} slides, got {len(v)}")
        return v

    @field_validator("filters")
    @classmethod
    def _filters(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        clean: dict[str, list[str]] = {}
        for dim, values in v.items():
            if not re.match(r"^[a-z_]{2,30}$", dim):
                raise ValueError(f"invalid filter dimension {dim!r}")
            vals = [str(x).strip() for x in values if str(x).strip()]
            if any(len(x) > 60 for x in vals):
                raise ValueError("filter value too long")
            if vals:
                clean[dim] = sorted(set(vals))
        return clean

    # ------------------------------------------------------------------ governance
    def validate_against(self, domain, dataset) -> list[str]:
        """Return a list of problems (empty = valid). Never raises for user content."""
        problems: list[str] = []
        if self.domain != domain.key:
            problems.append(f"spec is for domain {self.domain!r}, dataset is {domain.key!r}")
        if self.template not in domain.templates:
            problems.append(f"unknown template {self.template!r}")
        for s in self.slides:
            if s.slide_type not in domain.slide_catalog:
                problems.append(f"slide type {s.slide_type!r} is not in the approved catalog")
        for dim, values in self.filters.items():
            if dim not in domain.filter_dimensions:
                problems.append(f"{dim!r} is not an approved filter dimension")
                continue
            allowed = set(dataset.dimension_values(dim))
            bad = [x for x in values if x not in allowed]
            if bad:
                problems.append(f"{dim}: {', '.join(bad)} not present in dataset {dataset.version}")
        available = set(dataset.months)
        missing = [m for m in self.months if m not in available]
        if missing:
            problems.append(f"months {', '.join(missing)} are not in dataset {dataset.version}")
        if not self.months:
            problems.append("no reporting months selected")
        return problems


def month_label(m: str) -> str:
    import calendar

    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y}"


def period_label(months: list[str]) -> str:
    if not months:
        return "no period"
    months = sorted(months)
    if len(months) == 1:
        return month_label(months[0])
    y0, m0 = map(int, months[0].split("-"))
    y1, m1 = map(int, months[-1].split("-"))
    contiguous = len(months) == (y1 - y0) * 12 + (m1 - m0) + 1
    if contiguous and y0 == y1 and len(months) == 3 and m0 % 3 == 1:
        return f"Q{(m0 - 1) // 3 + 1} {y0}"
    if contiguous and y0 == y1 and len(months) == 6 and m0 in (1, 7):
        return f"H{1 if m0 == 1 else 2} {y0}"
    if contiguous and y0 == y1 and len(months) == 12:
        return f"FY {y0}"
    if contiguous:
        a, b = month_label(months[0]), month_label(months[-1])
        return f"{a.split()[0]}–{b}" if y0 == y1 else f"{a}–{b}"
    return ", ".join(month_label(m) for m in months)


def shift_months(months: list[str], delta: int) -> list[str]:
    out = []
    for m in months:
        y, mo = map(int, m.split("-"))
        idx = y * 12 + (mo - 1) + delta
        out.append(f"{idx // 12:04d}-{idx % 12 + 1:02d}")
    return out


def comparison_months(spec: DeckSpec) -> list[str]:
    c = spec.comparison
    if c.mode == "none" or not spec.months:
        return []
    if c.mode == "custom":
        return list(c.months)
    if c.mode == "yoy":
        return shift_months(spec.months, -12)
    months = sorted(spec.months)
    y0, m0 = map(int, months[0].split("-"))
    y1, m1 = map(int, months[-1].split("-"))
    span = (y1 - y0) * 12 + (m1 - m0) + 1
    return shift_months(months, -span)
