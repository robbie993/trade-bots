"""A shadow book that holds weights of equity, steps one close at a time.

Each `step(day, closes)`:
  1. earns yesterday-close -> today-close on what it holds (weights drift),
  2. fills whatever was decided at yesterday's close, at today's close, paying
     costs,
  3. lets the strategy decide at today's close; that fills tomorrow.

The whole book serialises to a dict, so the forward runner can stop after
any day and pick up again later with nothing lost.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import rules as R


@dataclass
class WeightBook:
    name: str
    equity: float = R.START_EQUITY
    weights: dict = field(default_factory=dict)      # symbol -> share of equity
    pending: dict | None = None                      # target decided yesterday
    last_day: str | None = None
    turnover: float = 0.0
    trades: int = 0
    costs_paid: float = 0.0
    history: list = field(default_factory=list)      # [day, equity, invested]
    memo: dict = field(default_factory=dict)         # strategy state (e.g. R3-B entries)

    def earn(self, day: str, rets: dict, cash_rate: float) -> None:
        """Yesterday's close to today's, from one consistent adjusted price fetch."""
        if self.last_day is not None:
            growth = 0.0
            new_w = {}
            for s, w in self.weights.items():
                r = rets.get(s, 0.0)
                growth += w * r
                new_w[s] = w * (1 + r)
            growth += (1 - sum(self.weights.values())) * cash_rate
            self.equity *= 1 + growth
            self.weights = {s: v / (1 + growth) for s, v in new_w.items() if v > 1e-12}
        self.last_day = day

    def fill(self, cost_of) -> None:
        if self.pending is None:
            return
        target = {s: w for s, w in self.pending.items() if w > 1e-9}
        cost = 0.0
        for s in set(target) | set(self.weights):
            dw = abs(target.get(s, 0.0) - self.weights.get(s, 0.0))
            if dw > 1e-9:
                cost += dw * cost_of(s)
                self.turnover += dw
                self.trades += 1
        self.equity *= 1 - cost
        self.costs_paid += cost
        self.weights = target
        self.pending = None

    def record(self, day: str) -> None:
        self.history.append([day, round(self.equity, 2), round(sum(self.weights.values()), 4)])

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "WeightBook":
        return cls(**d)


def stock_cost(large: set):
    """Per-side cost for a symbol: half-spread by size class, plus slippage."""
    def cost_of(s: str) -> float:
        half = R.HALF_SPREAD_LARGE if s in large else R.HALF_SPREAD_SMALL
        return half + R.SLIPPAGE
    return cost_of


def cagr(history: list) -> float:
    if len(history) < 2:
        return 0.0
    g = history[-1][1] / R.START_EQUITY
    return g ** (252 / (len(history) - 1)) - 1 if g > 0 else -1.0


def max_dd(history: list) -> float:
    peak, worst = -math.inf, 0.0
    for _, eq, _ in history:
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    return worst
