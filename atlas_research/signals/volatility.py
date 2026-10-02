"""Realised volatility (Moreira & Muir 2017, "Volatility-Managed Portfolios").

Annualised standard deviation of daily returns over a fixed set of lookbacks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACKS = (21, 42, 63, 126)


def realized_vol(returns: pd.DataFrame, n: int) -> pd.DataFrame:
    return returns.rolling(n, min_periods=int(n * 0.8)).std() * np.sqrt(252)
