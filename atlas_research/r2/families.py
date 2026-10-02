"""R2 families: three architectures, each with its own bounded genome.

Every family maps (market, genome, day t) to a target book. The GA tunes
genes inside one family and can never move a genome into another family.
Rules shared by all families (trend permission, the SPY trend state and the
exposure ladder) are fixed here and documented in PREREG_R2.md.
"""
from __future__ import annotations

import numpy as np

from ..portfolio.engine import Market
from ..signals import momentum

SMA_CHOICES = (100, 150, 200, 250)
NORMAL, WEAK, BEAR, CRASH = 0, 1, 2, 3
STATE_NAMES = ("normal", "weakening", "bear", "crash")

MOMENTUM_GENES = {
    "mom_w21": ("float", 0.0, 0.40),
    "mom_w63": ("float", 0.0, 0.40),
    "mom_w126": ("float", 0.0, 0.50),
    "mom_w252": ("float", 0.0, 0.60),
    "skip_recent": ("choice", (0, 5)),
}
LADDER_GENES = {
    "exp_normal": ("float", 1.00, 1.20),
    "exp_weak": ("float", 0.70, 1.00),
    "exp_bear": ("float", 0.30, 0.70),
    "exp_crash": ("float", 0.00, 0.40),
}
TRADING_GENES = {
    "sma": ("choice", SMA_CHOICES),
    "rebalance_days": ("choice", (5, 21)),
    "band": ("float", 0.0, 0.05),
}

SPACES = {
    "A": {**MOMENTUM_GENES, **TRADING_GENES,
          "core_w": ("float", 0.60, 0.80),
          "top_n": ("int", 1, 3),
          "fallback": ("choice", (0, 1, 2)),          # cash / SPY / IEF
          "gross_normal": ("float", 1.00, 1.20)},
    "B": {**MOMENTUM_GENES, **TRADING_GENES, **LADDER_GENES,
          "top_n": ("int", 1, 5),
          "inverse_vol": ("choice", (0, 1)),
          "below_frac": ("choice", (0.0, 0.5))},
    "D": {**MOMENTUM_GENES, **TRADING_GENES,
          "target_vol": ("float", 0.10, 0.20),
          "vol_lb": ("choice", (21, 63)),
          "spy_min": ("float", 0.20, 0.60),
          "spy_max": ("float", 1.00, 1.20),
          "overlay_w": ("float", 0.00, 0.30),
          "overlay_n": ("int", 1, 2)},
}

DEFAULTS = {
    "A": dict(mom_w21=0.1, mom_w63=0.2, mom_w126=0.3, mom_w252=0.4, skip_recent=5,
              sma=200, rebalance_days=21, band=0.02, core_w=0.70, top_n=2, fallback=1,
              gross_normal=1.0),
    "B": dict(mom_w21=0.1, mom_w63=0.2, mom_w126=0.3, mom_w252=0.4, skip_recent=5,
              sma=200, rebalance_days=21, band=0.02, exp_normal=1.0, exp_weak=0.85,
              exp_bear=0.5, exp_crash=0.2, top_n=3, inverse_vol=0, below_frac=0.0),
    "D": dict(mom_w21=0.1, mom_w63=0.2, mom_w126=0.3, mom_w252=0.4, skip_recent=5,
              sma=200, rebalance_days=21, band=0.02, target_vol=0.15, vol_lb=21,
              spy_min=0.4, spy_max=1.0, overlay_w=0.2, overlay_n=1),
}

NAMES = {"A": "R2-A SPY core + momentum sleeve",
         "B": "R2-B cross-sectional ETF momentum",
         "D": "R2-D vol-sized SPY + momentum overlay"}


class R2Market:
    """R1's Market plus moving averages and the SPY trend state."""

    def __init__(self, m: Market):
        self.m = m
        P = m.prices
        self.sma = {n: P.rolling(n, min_periods=n).mean().to_numpy(float) for n in SMA_CHOICES}
        spy = P.iloc[:, m.bench]
        r = spy.pct_change()
        self.spy_dd = (spy / spy.rolling(252, min_periods=60).max() - 1).fillna(0).to_numpy(float)
        self.spy_vol21 = (r.rolling(21).std() * np.sqrt(252)).fillna(0).to_numpy(float)
        self.spy_r63 = np.nan_to_num(m.mom[(63, 0)][:, m.bench])
        self.ief = m.symbols.index("IEF")

    def state(self, t: int, sma: int) -> int:
        b = self.m.bench
        avg = self.sma[sma][t, b]
        gap = self.m.P[t, b] / avg - 1 if np.isfinite(avg) else 0.0
        if self.spy_dd[t] <= -0.20 and self.spy_vol21[t] >= 0.30:
            return CRASH
        if gap <= -0.03 and self.spy_dd[t] <= -0.10:
            return BEAR
        if gap < 0 or self.spy_r63[t] < 0:
            return WEAK
        return NORMAL

    def permitted(self, t: int, sma: int) -> np.ndarray:
        avg = self.sma[sma][t]
        return np.where(np.isfinite(avg), self.m.P[t] > avg, False)


def momentum_score(rm: R2Market, g: dict, t: int, idx: np.ndarray) -> np.ndarray:
    m = rm.m
    w = np.array([g["mom_w21"], g["mom_w63"], g["mom_w126"], g["mom_w252"]])
    w = w / w.sum() if w.sum() > 1e-9 else np.full(4, 0.25)
    score = np.zeros(len(idx))
    for wn, n in zip(w, momentum.LOOKBACKS):
        if wn > 0:
            score += wn * np.nan_to_num(momentum.cross_sectional_z(m.mom[(n, g["skip_recent"])][t, idx]))
    return score


def _top(rm, g, t, n, exclude=()):
    elig = rm.m.eligible[t].copy()
    for i in exclude:
        elig[i] = False
    idx = np.flatnonzero(elig)
    if len(idx) == 0:
        return idx
    s = momentum_score(rm, g, t, idx)
    return idx[np.argsort(-s)[:n]]


def target_A(rm: R2Market, g: dict, t: int):
    m = rm.m
    st = rm.state(t, g["sma"])
    w = np.zeros(len(m.symbols))
    w[m.bench] = g["core_w"]
    sleeve = (g["gross_normal"] if st == NORMAL else 1.0) - g["core_w"]
    picks = _top(rm, g, t, g["top_n"], exclude=(m.bench,))
    ok = rm.permitted(t, g["sma"])
    slot = sleeve / g["top_n"]
    for i in picks:
        if ok[i]:
            w[i] += slot
        elif g["fallback"] == 1:
            w[m.bench] += slot
        elif g["fallback"] == 2 and m.eligible[t, rm.ief]:
            w[rm.ief] += slot
    if g["fallback"] == 1 and len(picks) < g["top_n"]:
        w[m.bench] += slot * (g["top_n"] - len(picks))
    return w, st


def target_B(rm: R2Market, g: dict, t: int):
    m = rm.m
    st = rm.state(t, g["sma"])
    w = np.zeros(len(m.symbols))
    picks = _top(rm, g, t, g["top_n"])
    if len(picks) == 0:
        return w, st
    if g["inverse_vol"]:
        v = np.maximum(np.nan_to_num(m.vol[63][t, picks], nan=0.2), 0.02)
        base = (1 / v) / (1 / v).sum()
    else:
        base = np.full(len(picks), 1 / g["top_n"])
    ok = rm.permitted(t, g["sma"])[picks]
    base = np.where(ok, base, base * g["below_frac"])
    level = (g["exp_normal"], g["exp_weak"], g["exp_bear"], g["exp_crash"])[st]
    w[picks] = base * level
    return w, st


def target_D(rm: R2Market, g: dict, t: int):
    m = rm.m
    st = rm.state(t, g["sma"])
    w = np.zeros(len(m.symbols))
    vol = m.vol[g["vol_lb"]][t, m.bench]
    spy_w = g["target_vol"] / vol if np.isfinite(vol) and vol > 0 else g["spy_min"]
    hi = g["spy_max"] if st == NORMAL else 1.0
    w[m.bench] = float(np.clip(spy_w, g["spy_min"], hi))
    picks = _top(rm, g, t, g["overlay_n"], exclude=(m.bench,))
    ok = rm.permitted(t, g["sma"])
    for i in picks:
        if ok[i]:
            w[i] += g["overlay_w"] / g["overlay_n"]
    if w.sum() > 1.2:
        w *= 1.2 / w.sum()
    return w, st


TARGETS = {"A": target_A, "B": target_B, "D": target_D}
