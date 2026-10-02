"""What a trade costs, per side, in basis points of the amount traded.

    cost = era multiplier x (half-spread + slippage) x stress multipliers
           + SEC fee on sells

* **Half-spread**: typical quoted half-spread for each ETF in recent years.
  Spreads were wider before decimal liquidity matured, so they are scaled up
  by era: 3x before 2008, 1.5x 2008-2012, 1x after.
* **Slippage**: 2 bp a side on everything, for not trading exactly at the
  close and for moving the price.
* **Commission**: Alpaca charges none on ETFs. The SEC fee (about 0.28 bp of
  sell value) is charged on half of all turnover.

`spread_mult` and `slippage_mult` are what the gauntlet turns up to 2x and 3x.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HALF_SPREAD_BPS = {
    "SPY": 0.5, "QQQ": 0.5, "IWM": 0.5, "EFA": 1.0, "EEM": 1.0, "TLT": 0.5,
    "IEF": 1.0, "LQD": 1.0, "HYG": 1.0, "GLD": 0.5, "SLV": 1.0, "DBC": 3.0,
    "VNQ": 1.0, "UUP": 2.0,
}
SLIPPAGE_BPS = 2.0
SEC_FEE_BPS = 0.28


def era_multiplier(index: pd.DatetimeIndex) -> np.ndarray:
    y = index.year
    return np.where(y < 2008, 3.0, np.where(y <= 2012, 1.5, 1.0))


def cost_matrix(index: pd.DatetimeIndex, symbols, spread_mult: float = 1.0,
                slippage_mult: float = 1.0) -> np.ndarray:
    """Per-day, per-asset cost as a fraction of the amount traded."""
    era = era_multiplier(index)[:, None]
    half = np.array([HALF_SPREAD_BPS[s] for s in symbols])[None, :]
    bps = era * (half * spread_mult + SLIPPAGE_BPS * slippage_mult) + SEC_FEE_BPS / 2
    return bps / 1e4
