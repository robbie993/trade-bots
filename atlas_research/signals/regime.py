"""The panic score (after Daniel & Moskowitz 2016, "Momentum Crashes").

Momentum's worst losses came after market declines, in high volatility, when
the market snapped back. Each input below is observable at the close it is
dated, mapped onto 0 (calm) .. 1 (panic) with fixed scales chosen before any
backtest ran, so the GA can only re-weight them:

  drawdown      SPY's fall from its 252-day high; 0 at the high, 1 at -20%
  volatility    SPY 21-day realised vol; 0 at 12%, 1 at 40%
  vol_accel     10-day vol over 63-day vol; 0 at 1.0x, 1 at 2.0x
  breadth       share of the ETFs trading below their 200-day average
  trend         SPY under its 200-day average, plus its 21-day loss; 0 at or
                above the average, 1 at 5% below or a 10% monthly loss
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COMPONENTS = ("drawdown", "volatility", "vol_accel", "breadth", "trend")


def _ramp(x, lo, hi):
    return ((x - lo) / (hi - lo)).clip(0, 1)


def components(prices: pd.DataFrame, bench: str = "SPY") -> pd.DataFrame:
    spy = prices[bench]
    r = spy.pct_change()
    dd = spy / spy.rolling(252, min_periods=60).max() - 1
    vol21 = r.rolling(21).std() * np.sqrt(252)
    vol10 = r.rolling(10).std() * np.sqrt(252)
    vol63 = r.rolling(63).std() * np.sqrt(252)
    sma = prices.rolling(200, min_periods=200).mean()
    below = (prices < sma).where(sma.notna())
    breadth = below.mean(axis=1, skipna=True)
    spy_gap = spy / sma[bench] - 1
    trend = np.maximum(_ramp(-spy_gap, 0, 0.05), _ramp(-(spy / spy.shift(21) - 1), 0, 0.10))
    return pd.DataFrame({
        "drawdown": _ramp(-dd, 0, 0.20),
        "volatility": _ramp(vol21, 0.12, 0.40),
        "vol_accel": _ramp(vol10 / vol63, 1.0, 2.0),
        "breadth": breadth,
        "trend": trend,
    }).fillna(0.0)
