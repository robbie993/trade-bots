"""The daily backtest: score, filter, size, trade, drift.

Timing is the part that most often lies in a backtest, so it is spelled out:

* At the **close of day t** Atlas reads only data dated t or earlier and
  decides a target book.
* That book is **traded at the close of day t+1**, paying that day's costs,
  and first earns the return from t+1 to t+2. One full day of delay, so no
  decision ever trades on the price it was computed from.
* Whatever is not invested sits in cash and earns the T-bill rate.

Layers, in order (`target`): momentum score -> value and low-vol tilts ->
pick top N -> time-series trend filter (failing slots go to cash, not to
the next name) -> equal or inverse-vol weights, capped -> volatility target
(scale down only, never above 100%) -> panic regime multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..data import rates
from ..evolution.genome import Genome
from ..signals import momentum, regime, value, volatility
from ..validation.costs import cost_matrix

MIN_HISTORY = 260   # an ETF is investable once it has a year of prices


class Market:
    """Every signal precomputed once, as numpy arrays indexed [day, asset]."""

    def __init__(self, prices: pd.DataFrame, bench: str = "SPY"):
        self.prices = prices
        self.dates = prices.index
        self.symbols = list(prices.columns)
        self.bench = self.symbols.index(bench)
        P = prices.to_numpy(float)
        self.P = P
        rets = prices.pct_change()
        self.R = rets.to_numpy(float)
        rn = prices.shift(-1) / prices - 1
        self.rnext = np.nan_to_num(rn.to_numpy(float), nan=0.0)

        first = np.argmax(~np.isnan(P), axis=0)
        days = np.arange(len(P))[:, None]
        self.eligible = (days >= first[None, :] + MIN_HISTORY) & ~np.isnan(P)

        self.cash = rates.daily_cash_returns(self.dates).to_numpy(float)
        cash_index = np.cumprod(1 + self.cash)
        self.mom = {}
        self.cash_over = {}
        for n in momentum.LOOKBACKS:
            for s in momentum.SKIPS:
                self.mom[(n, s)] = momentum.trailing_return(prices, n, s).to_numpy(float)
            ci = pd.Series(cash_index)
            self.cash_over[n] = (ci / ci.shift(n) - 1).to_numpy(float)
        self.value = value.five_year_reversal(prices).to_numpy(float)
        self.vol = {n: volatility.realized_vol(rets, n).to_numpy(float)
                    for n in volatility.LOOKBACKS}
        self.panic_parts = regime.components(prices, bench).to_numpy(float)
        self._costs = {}

    def costs(self, spread_mult=1.0, slippage_mult=1.0) -> np.ndarray:
        k = (spread_mult, slippage_mult)
        if k not in self._costs:
            self._costs[k] = cost_matrix(self.dates, self.symbols, spread_mult, slippage_mult)
        return self._costs[k]

    def index_of(self, day) -> int:
        return int(self.dates.searchsorted(pd.Timestamp(day)))

    def span(self, start, end) -> tuple[int, int]:
        """Day indices (s, e): decide from s, returns for days s+1 .. e,
        with e the last trading day on or before `end`."""
        s = self.index_of(start)
        e = min(int(self.dates.searchsorted(pd.Timestamp(end), side="right")) - 1,
                len(self.dates) - 1)
        return s, e


@dataclass
class Ablation:
    """Switch a layer off, to measure what it contributed."""
    value: bool = True
    lowvol: bool = True
    trend_filter: bool = True
    vol_target: bool = True
    regime: bool = True


@dataclass
class Run:
    dates: pd.DatetimeIndex
    ret: np.ndarray            # strategy daily return, net of costs
    bench: np.ndarray          # SPY total return, same days
    cash: np.ndarray           # T-bill daily return, same days
    exposure: np.ndarray       # share of the book invested, start of each day
    weights: np.ndarray        # [day, asset] held during each day
    turnover: float            # sum of |trade| as a share of equity
    trades: int                # asset-level trades executed
    costs_paid: float          # sum of costs as a share of equity
    regime_state: np.ndarray   # 0 normal, 1 warning, 2 panic (as decided)
    symbols: list = field(default_factory=list)


def target(m: Market, g: Genome, t: int, universe: Optional[np.ndarray] = None,
           ab: Ablation = Ablation()) -> tuple[np.ndarray, int]:
    """The book Atlas wants at the close of day t, and its regime state."""
    N = len(m.symbols)
    w = np.zeros(N)
    elig = m.eligible[t].copy()
    if universe is not None:
        elig &= universe
    idx = np.flatnonzero(elig)
    if len(idx) == 0:
        return w, 0

    mw = g.mom_weights()
    skip = g.skip_recent
    mom_z = np.zeros(len(idx))
    abs_mom = np.zeros(len(idx))
    over_cash = np.zeros(len(idx))
    for n, wn in mw.items():
        if wn <= 0:
            continue
        raw = m.mom[(n, skip)][t, idx]
        mom_z += wn * np.nan_to_num(momentum.cross_sectional_z(raw))
        abs_mom += wn * np.nan_to_num(raw)
        over_cash += wn * np.nan_to_num(raw - m.cash_over[n][t])

    vol_now = m.vol[g.vol_lookback][t, idx]
    score = g.momentum_share() * mom_z
    if ab.value and g.value_w > 0:
        score += g.value_w * np.nan_to_num(momentum.cross_sectional_z(m.value[t, idx]))
    if ab.lowvol and g.lowvol_w > 0:
        score += g.lowvol_w * np.nan_to_num(-momentum.cross_sectional_z(vol_now))

    order = np.argsort(-score)[: g.top_n]
    picks = idx[order]
    if g.inverse_vol:
        iv = 1 / np.maximum(np.nan_to_num(vol_now[order], nan=0.2), 0.02)
        raw_w = iv / iv.sum()
    else:
        raw_w = np.full(len(picks), 1 / len(picks))
    if ab.trend_filter and g.trend_filter:
        test = abs_mom[order] if g.trend_filter == 1 else over_cash[order]
        raw_w = np.where(test > 0, raw_w, 0.0)
    raw_w = np.minimum(raw_w, g.position_cap)
    w[picks] = raw_w
    if w.sum() <= 0:
        return w, 0

    if ab.vol_target:
        L = g.vol_lookback
        held = np.flatnonzero(w > 0)
        hist = m.R[max(0, t - L + 1): t + 1][:, held]
        hist = hist[~np.isnan(hist).any(axis=1)]
        if len(hist) > 10:
            cov = np.cov(hist, rowvar=False) * 252
            pv = float(np.sqrt(max(w[held] @ np.atleast_2d(cov) @ w[held], 1e-12)))
            w *= min(1.0, g.target_vol / pv)

    state = 0
    if ab.regime:
        panic = float(np.dot(g.panic_weights(), m.panic_parts[t]))
        if panic >= g.panic_threshold:
            w *= g.panic_exposure
            state = 2
        elif panic >= g.warn_threshold:
            w *= g.warn_exposure
            state = 1
    return w, state


def regime_state(m: Market, g: Genome, t: int) -> int:
    panic = float(np.dot(g.panic_weights(), m.panic_parts[t]))
    return 2 if panic >= g.panic_threshold else (1 if panic >= g.warn_threshold else 0)


def backtest(m: Market, g: Genome, start, end, universe: Optional[np.ndarray] = None,
             spread_mult: float = 1.0, slippage_mult: float = 1.0,
             ab: Ablation = Ablation()) -> Run:
    s, e = m.span(start, end)
    N = len(m.symbols)
    C = m.costs(spread_mult, slippage_mult)
    n_days = e - s
    ret = np.zeros(n_days)
    expo = np.zeros(n_days)
    W = np.zeros((n_days, N))
    states = np.zeros(n_days, dtype=np.int8)
    w = np.zeros(N)
    pending = None
    peak = np.zeros(N)
    turnover = 0.0
    trades = 0
    paid = 0.0
    applied_state = 0
    since_rebal = 0

    for k, t in enumerate(range(s, e)):
        cost = 0.0
        # 1. Execute what was decided yesterday, at today's close.
        if pending is not None:
            new = pending
            if g.rebalance_threshold > 0:
                small = (np.abs(new - w) < g.rebalance_threshold) & (new > 0) & (w > 0)
                new = np.where(small, w, new)
                # keeping a drifted weight must not push the book past 100%
                if new.sum() > 1.0:
                    new = new / new.sum()
            dw = np.abs(new - w)
            traded = dw > 1e-9
            cost = float(dw @ C[t])
            turnover += float(dw.sum())
            trades += int(traded.sum())
            paid += cost
            entered = traded & (new > 0) & (w <= 0)
            peak[entered] = m.P[t, entered]
            w = new
            pending = None

        # 2. Decide at today's close, for tomorrow.
        if since_rebal % g.rebalance_days == 0:
            pending, applied_state = target(m, g, t, universe, ab)
        else:
            alarm = False
            nw = w.copy()
            if g.stop_rule > 0:
                held = w > 0
                peak[held] = np.maximum(peak[held], np.nan_to_num(m.P[t, held]))
                hit = held & (m.P[t] < peak * (1 - g.stop_rule))
                if hit.any():
                    nw[hit] = 0.0
                    alarm = True
            if ab.regime and g.panic_daily:
                st = regime_state(m, g, t)
                if st > applied_state:
                    mult_now = (1, g.warn_exposure, g.panic_exposure)
                    nw *= mult_now[st] / mult_now[applied_state]
                    applied_state = st
                    alarm = True
            if alarm:
                pending = nw
        since_rebal += 1

        # 3. Earn the next day's return on what is held now.
        r = m.rnext[t]
        invested = w.sum()
        gross = float(w @ r) + (1 - invested) * m.cash[t + 1]
        ret[k] = gross - cost
        expo[k] = invested
        W[k] = w
        states[k] = applied_state
        grown = w * (1 + r)
        w = grown / (1 + gross) if (1 + gross) > 0 else grown

    days = m.dates[s + 1: e + 1]
    return Run(dates=days, ret=ret, bench=m.rnext[s:e, m.bench].copy(),
               cash=m.cash[s + 1: e + 1].copy(), exposure=expo, weights=W,
               turnover=turnover, trades=trades, costs_paid=paid,
               regime_state=states, symbols=m.symbols)
