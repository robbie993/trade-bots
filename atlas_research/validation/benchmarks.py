"""The five benchmarks every Atlas result is printed beside.

A  SPY buy-and-hold, dividends reinvested (total-return index).
B  SPY + cash at the candidate's own average exposure, rebalanced daily.
   If Atlas only beats SPY on drawdown because it held 70% SPY and 30%
   T-bills, B shows it.
C  Simple momentum: monthly, the 3 ETFs with the best 12-month return
   (skipping the last week), equal weight, each only if its own 12-month
   return is positive; otherwise that slot sits in cash. Same costs as Atlas.
D  Volatility-managed SPY (Moreira & Muir): SPY scaled to 12% annualised
   vol on its 21-day realised vol, capped at 100%, one-day lag, same costs.
E  60/40: 60% SPY, 40% IEF (7-10y Treasuries), rebalanced monthly. Before
   IEF existed (mid 2003) the 40% sits in cash.
"""
from __future__ import annotations

import numpy as np

from ..evolution.genome import Genome
from ..portfolio.engine import Ablation, Market, backtest

SIMPLE_MOMENTUM = Genome(
    mom_w21=0.0, mom_w63=0.0, mom_w126=0.0, mom_w252=0.6, skip_recent=5,
    value_w=0.0, lowvol_w=0.0, trend_filter=1, top_n=3, inverse_vol=0,
    position_cap=1.0, rebalance_days=21, rebalance_threshold=0.0, stop_rule=0.0,
    panic_daily=0,
)


def _slice(m: Market, start, end):
    return m.span(start, end)


def spy(m: Market, start, end) -> np.ndarray:
    s, e = _slice(m, start, end)
    return m.rnext[s:e, m.bench].copy()


def spy_cash(m: Market, start, end, exposure: float) -> np.ndarray:
    s, e = _slice(m, start, end)
    return exposure * m.rnext[s:e, m.bench] + (1 - exposure) * m.cash[s + 1:e + 1]


def simple_momentum(m: Market, start, end, **cost) -> np.ndarray:
    return backtest(m, SIMPLE_MOMENTUM, start, end,
                    ab=Ablation(vol_target=False, regime=False), **cost).ret


def vol_managed_spy(m: Market, start, end, target=0.12, spread_mult=1.0,
                    slippage_mult=1.0) -> np.ndarray:
    s, e = _slice(m, start, end)
    vol = m.vol[21][:, m.bench]
    C = m.costs(spread_mult, slippage_mult)[:, m.bench]
    out = np.zeros(e - s)
    w = 0.0
    for k, t in enumerate(range(s, e)):
        # decided at t-1's close, traded at t's close
        want = min(1.0, target / vol[t - 1]) if t > 0 and np.isfinite(vol[t - 1]) else 0.0
        cost = abs(want - w) * C[t]
        w = want
        out[k] = w * m.rnext[t, m.bench] + (1 - w) * m.cash[t + 1] - cost
    return out


def sixty_forty(m: Market, start, end) -> np.ndarray:
    s, e = _slice(m, start, end)
    ief = m.symbols.index("IEF")
    out = np.zeros(e - s)
    w_spy, w_ief = 0.6, 0.4
    for k, t in enumerate(range(s, e)):
        if k % 21 == 0:
            w_spy = 0.6
            w_ief = 0.4 if m.eligible[t, ief] else 0.0
        r_ief = m.rnext[t, ief] if w_ief > 0 else 0.0
        cash_w = 1 - w_spy - w_ief
        g = w_spy * m.rnext[t, m.bench] + w_ief * r_ief + cash_w * m.cash[t + 1]
        out[k] = g
        w_spy = w_spy * (1 + m.rnext[t, m.bench]) / (1 + g)
        w_ief = w_ief * (1 + r_ief) / (1 + g)
    return out


def all_benchmarks(m: Market, start, end, exposure: float) -> dict:
    return {
        "A_spy": spy(m, start, end),
        "B_spy_cash": spy_cash(m, start, end, exposure),
        "C_simple_momentum": simple_momentum(m, start, end),
        "D_vol_managed_spy": vol_managed_spy(m, start, end),
        "E_60_40": sixty_forty(m, start, end),
    }
