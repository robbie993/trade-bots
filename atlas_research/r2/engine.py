"""R2 execution: same timing and costs as R1, plus up to 120% gross.

Decide at close t, trade at close t+1. Money borrowed beyond 100% costs the
T-bill rate plus `BORROW_SPREAD`. Between scheduled rebalances the book is
re-decided at once when the SPY trend state gets worse; an improving state
waits for the next rebalance. Every trade lands at 120% or less; a book that
drifts more than 2 points above the cap between rebalances is trimmed back
the next day.
"""
from __future__ import annotations

import numpy as np

from ..portfolio.engine import Run
from .families import TARGETS, R2Market

MAX_GROSS = 1.20
BORROW_SPREAD = 0.02 / 252


def backtest(rm: R2Market, family: str, g: dict, start, end,
             spread_mult: float = 1.0, slippage_mult: float = 1.0) -> Run:
    m = rm.m
    target = TARGETS[family]
    s, e = m.span(start, end)
    N = len(m.symbols)
    C = m.costs(spread_mult, slippage_mult)
    n = e - s
    ret = np.zeros(n)
    expo = np.zeros(n)
    W = np.zeros((n, N))
    states = np.zeros(n, dtype=np.int8)
    w = np.zeros(N)
    pending = None
    turnover = 0.0
    trades = 0
    paid = 0.0
    applied = 0
    since = 0
    for k, t in enumerate(range(s, e)):
        cost = 0.0
        if pending is not None:
            new = pending
            if g["band"] > 0:
                keep = (np.abs(new - w) < g["band"]) & (new > 0) & (w > 0)
                new = np.where(keep, w, new)
            if new.sum() > MAX_GROSS:
                new = new * (MAX_GROSS / new.sum())
            dw = np.abs(new - w)
            cost = float(dw @ C[t])
            turnover += float(dw.sum())
            trades += int((dw > 1e-9).sum())
            paid += cost
            w = new
            pending = None
        if since % g["rebalance_days"] == 0:
            pending, applied = target(rm, g, t)
        else:
            st = rm.state(t, g["sma"])
            if st > applied:
                pending, applied = target(rm, g, t)
            elif w.sum() > MAX_GROSS + 0.02:
                # a levered book that drifted past the cap is trimmed back next day
                pending = w * (MAX_GROSS / w.sum())
        since += 1

        r = m.rnext[t]
        inv = w.sum()
        cash_leg = 1 - inv
        rate = m.cash[t + 1] + (BORROW_SPREAD if cash_leg < 0 else 0.0)
        gross = float(w @ r) + cash_leg * rate
        ret[k] = gross - cost
        expo[k] = inv
        W[k] = w
        states[k] = applied
        grown = w * (1 + r)
        w = grown / (1 + gross) if 1 + gross > 0 else grown
    return Run(dates=m.dates[s + 1:e + 1], ret=ret, bench=m.rnext[s:e, m.bench].copy(),
               cash=m.cash[s + 1:e + 1].copy(), exposure=expo, weights=W, turnover=turnover,
               trades=trades, costs_paid=paid, regime_state=states, symbols=m.symbols)
