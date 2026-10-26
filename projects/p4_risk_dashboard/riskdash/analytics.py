"""Small, explainable statistics used by the agent's tools.

Nothing here is a black box: each function is a few lines of numpy that an
interviewer can follow.

* ``onset``: when did a move start? Fits two simple models at every candidate
  date (a level shift, and a flat line followed by a ramp) and keeps the date
  and model with the lowest squared error.
* ``theil_sen``: a robust trend line (the median of all pairwise slopes), not
  pulled around by one outlier day.
* ``project_crossing``: extends that trend and reports the first day it crosses
  the early-warning trigger or the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd


@dataclass
class Onset:
    onset: date
    kind: str  # "step" or "ramp"
    before: float  # level before the onset
    after: float  # level at the end of the window
    error_reduction_pct: float  # how much better the fit is than "no change"


def onset(series: pd.Series, end: date, lookback: int = 90, min_tail: int = 2) -> Onset | None:
    s = series[(series.index <= end) & (series.index > end - timedelta(days=lookback))].dropna()
    if len(s) < 10:
        return None
    y = s.to_numpy(dtype=float)
    n = len(y)
    x = np.arange(n, dtype=float)
    sse0 = float(((y - y.mean()) ** 2).sum())
    if sse0 == 0:
        return None
    best: tuple[float, int, str] | None = None
    for k in range(5, n - min_tail + 1):
        pre, post = y[:k], y[k:]
        # Level shift at k.
        sse_step = float(((pre - pre.mean()) ** 2).sum() + ((post - post.mean()) ** 2).sum())
        # Flat until k, then a straight line starting from the pre-k level.
        xp = x[k:] - x[k] + 1
        slope = float(((post - pre.mean()) * xp).sum() / (xp * xp).sum())
        sse_ramp = float(((pre - pre.mean()) ** 2).sum() + ((post - pre.mean() - slope * xp) ** 2).sum())
        for sse, kind in ((sse_step, "step"), (sse_ramp, "ramp")):
            if best is None or sse < best[0] - 1e-12:
                best = (sse, k, kind)
    assert best is not None
    sse, k, kind = best
    return Onset(onset=s.index[k], kind=kind, before=float(y[:k].mean()), after=float(y[-1]),
                 error_reduction_pct=round(100 * (1 - sse / sse0), 1))


def theil_sen(series: pd.Series, end: date, window: int = 21) -> tuple[float, float] | None:
    """Return (slope per day, fitted value at ``end``)."""
    s = series[(series.index <= end) & (series.index > end - timedelta(days=window))].dropna()
    if len(s) < 5:
        return None
    t = np.array([(d - end).days for d in s.index], dtype=float)
    y = s.to_numpy(dtype=float)
    i, j = np.triu_indices(len(t), k=1)
    slopes = (y[j] - y[i]) / (t[j] - t[i])
    slope = float(np.median(slopes))
    intercept = float(np.median(y - slope * t))
    return slope, intercept


def project_crossing(current: float, slope: float, threshold: float | None, worse_when_higher: bool,
                     horizon: int) -> int | None:
    """Days until a straight-line projection crosses ``threshold`` (None if not within the horizon)."""
    if threshold is None or slope == 0:
        return None
    if worse_when_higher and (slope <= 0 or current > threshold):
        return None
    if not worse_when_higher and (slope >= 0 or current < threshold):
        return None
    days = (threshold - current) / slope
    if days < 0 or days > horizon:
        return None
    return int(np.ceil(days)) if days > 0 else 0
