"""Time-series and cross-sectional momentum (Moskowitz, Ooi & Pedersen 2012;
Jegadeesh & Titman 1993).

Every lookback is fixed in advance at 21/63/126/252 trading days. The GA may
re-weight them; it may not invent a 37-day lookback. A "skip" drops the most
recent days, because very short-term returns tend to reverse and would
otherwise contaminate the longer signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACKS = (21, 63, 126, 252)
SKIPS = (0, 5)


def trailing_return(prices: pd.DataFrame, n: int, skip: int = 0) -> pd.DataFrame:
    """Return from close t-n to close t-skip, known at the close of t."""
    return prices.shift(skip) / prices.shift(n) - 1


def cross_sectional_z(x: np.ndarray) -> np.ndarray:
    """z-score across the non-NaN entries of a row; NaN stays NaN."""
    ok = ~np.isnan(x)
    if ok.sum() < 2:
        return np.where(ok, 0.0, np.nan)
    mu = x[ok].mean()
    sd = x[ok].std()
    if sd < 1e-12:
        return np.where(np.isnan(x), np.nan, 0.0)
    return (x - mu) / sd
