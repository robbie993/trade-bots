"""Performance numbers for a daily return stream, always beside SPY's.

Sharpe and Sortino are computed on return **over cash**. A book that sits in
T-bills therefore has an excess return of zero and a Sharpe of zero, not an
infinite one, which closes the "idler" loophole where doing nothing scored
best because its volatility was nil.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TD = 252


def cagr(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    g = float(np.prod(1 + r))
    return g ** (TD / len(r)) - 1 if g > 0 else -1.0


def max_drawdown(r: np.ndarray) -> float:
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float((eq / peak - 1).min()) if len(r) else 0.0


def sharpe(r: np.ndarray, cash: np.ndarray) -> float:
    x = r - cash
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd * math.sqrt(TD)) if sd > 1e-12 else 0.0


def downside_dev(r: np.ndarray, cash: np.ndarray) -> float:
    x = np.minimum(r - cash, 0)
    return float(math.sqrt((x ** 2).mean()) * math.sqrt(TD)) if len(x) else 0.0


def sortino(r: np.ndarray, cash: np.ndarray) -> float:
    dd = downside_dev(r, cash)
    return float((r - cash).mean() * TD / dd) if dd > 1e-12 else 0.0


def calmar(r: np.ndarray) -> float:
    mdd = max_drawdown(r)
    return cagr(r) / abs(mdd) if mdd < -1e-9 else 0.0


def worst_rolling(r: np.ndarray, n: int = TD) -> float:
    if len(r) < n:
        return float(np.prod(1 + r) - 1)
    lg = np.log1p(r)
    c = np.concatenate([[0.0], np.cumsum(lg)])
    return float(np.expm1((c[n:] - c[:-n]).min()))


def longest_underwater(r: np.ndarray) -> int:
    """Longest run of days below a previous equity high (recovery time)."""
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    best = cur = 0
    for under in eq < peak * (1 - 1e-12):
        cur = cur + 1 if under else 0
        best = max(best, cur)
    return best


@dataclass
class Stats:
    days: int
    cagr: float
    max_dd: float
    sharpe: float
    sortino: float
    calmar: float
    downside_dev: float
    vol: float
    worst_12m: float
    longest_underwater_days: int
    total_return: float

    def as_dict(self):
        return asdict(self)


def stats(r: np.ndarray, cash: np.ndarray) -> Stats:
    return Stats(
        days=len(r), cagr=cagr(r), max_dd=max_drawdown(r), sharpe=sharpe(r, cash),
        sortino=sortino(r, cash), calmar=calmar(r), downside_dev=downside_dev(r, cash),
        vol=float(r.std(ddof=1) * math.sqrt(TD)) if len(r) > 1 else 0.0,
        worst_12m=worst_rolling(r), longest_underwater_days=longest_underwater(r),
        total_return=float(np.prod(1 + r) - 1),
    )


def calendar_excess(dates: pd.DatetimeIndex, r: np.ndarray, b: np.ndarray) -> pd.Series:
    """Strategy minus SPY total return, per calendar year."""
    df = pd.DataFrame({"r": r, "b": b}, index=dates)
    g = df.groupby(df.index.year).apply(lambda x: np.prod(1 + x.r) - np.prod(1 + x.b))
    return g


def rolling_beat_share(r: np.ndarray, b: np.ndarray, n: int) -> float:
    """Share of rolling n-day windows in which the strategy beat SPY."""
    if len(r) < n:
        return float("nan")
    c1 = np.concatenate([[0], np.cumsum(np.log1p(r))])
    c2 = np.concatenate([[0], np.cumsum(np.log1p(b))])
    return float(((c1[n:] - c1[:-n]) > (c2[n:] - c2[:-n])).mean())


def year_concentration(excess_by_year: pd.Series) -> float:
    """Largest single year's share of the total positive excess return."""
    pos = excess_by_year[excess_by_year > 0]
    tot = excess_by_year.sum()
    if tot <= 0 or pos.empty:
        return float("nan")
    return float(pos.max() / tot)
