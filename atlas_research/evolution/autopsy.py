"""Why did a genome lose? The autopsy every genome gets.

A score says genome 17 is 1.73 and genome 18 is 1.69. An autopsy says
genome 17 lost 31% in 2022 because it stayed fully invested while the vol
filter reacted too slowly, which is something the next generation can
actually fix. `diagnose` turns the autopsy into failure tags, and
`descendants.py` turns tags into targeted mutations.

Market regimes are labelled from SPY with hindsight-free rules (each day's
label uses only data up to that day), and a day can carry several labels:

  bull       SPY above its 200-day average, 63-day return positive, vol < 25%
  bear       SPY 15%+ below its 252-day high and under its 200-day average
  high_vol   SPY 21-day vol above 25%
  low_vol    SPY 21-day vol below 12%
  sideways   SPY 126-day return within +/-5%
  recovery   SPY 10%+ below its high but up 10%+ over 63 days
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..portfolio.engine import Ablation, Market, Run, backtest
from ..validation import metrics as M

REGIMES = ("bull", "bear", "high_vol", "low_vol", "sideways", "recovery")


def regime_labels(m: Market) -> pd.DataFrame:
    spy = m.prices.iloc[:, m.bench]
    r = spy.pct_change()
    vol = r.rolling(21).std() * np.sqrt(252)
    sma = spy.rolling(200).mean()
    dd = spy / spy.rolling(252, min_periods=60).max() - 1
    r63 = spy / spy.shift(63) - 1
    r126 = spy / spy.shift(126) - 1
    return pd.DataFrame({
        "bull": (spy > sma) & (r63 > 0) & (vol < 0.25),
        "bear": (dd < -0.15) & (spy < sma),
        "high_vol": vol > 0.25,
        "low_vol": vol < 0.12,
        "sideways": r126.abs() < 0.05,
        "recovery": (dd < -0.10) & (r63 > 0.10),
    })


@dataclass
class Autopsy:
    excess_by_year: dict
    best_periods: list
    worst_periods: list
    max_dd: dict
    by_regime: dict
    longest_losing_months: int
    longest_winning_months: int
    contribution: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__


def _periods(dates, r, b, n=63, k=3):
    lr = np.log1p(r) - np.log1p(b)
    c = np.concatenate([[0], np.cumsum(lr)])
    if len(r) <= n:
        return [], []
    roll = c[n:] - c[:-n]
    picked_best, picked_worst = [], []
    for picked, order in ((picked_best, np.argsort(-roll)), (picked_worst, np.argsort(roll))):
        for i in order:
            if all(abs(i - j) >= n for j in picked):
                picked.append(int(i))
            if len(picked) == k:
                break
    fmt = lambda i: {"from": str(dates[i].date()), "to": str(dates[i + n - 1].date()),
                     "excess_vs_spy": float(np.expm1(roll[i]))}
    return [fmt(i) for i in picked_best], [fmt(i) for i in picked_worst]


def _max_dd(dates, r, expo):
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    trough = int(dd.argmin())
    top = int(np.argmax(eq[: trough + 1])) if trough > 0 else 0
    rec = np.flatnonzero(eq[trough:] >= peak[trough])
    return {"depth": float(dd[trough]), "peak": str(dates[top].date()),
            "trough": str(dates[trough].date()),
            "recovered": str(dates[trough + rec[0]].date()) if len(rec) else None,
            "mean_exposure_into_trough": float(expo[top: trough + 1].mean()) if trough > top else float(expo[trough])}


def _streaks(dates, r, b):
    df = pd.DataFrame({"r": r, "b": b}, index=dates)
    mo = df.groupby([df.index.year, df.index.month]).apply(lambda x: np.prod(1 + x.r) - np.prod(1 + x.b))
    win = lose = cw = cl = 0
    for x in mo:
        cw, cl = (cw + 1, 0) if x > 0 else (0, cl + 1)
        win, lose = max(win, cw), max(lose, cl)
    return lose, win


def autopsy(m: Market, run: Run, labels: pd.DataFrame | None = None) -> Autopsy:
    labels = regime_labels(m) if labels is None else labels
    lab = labels.reindex(run.dates).fillna(False)
    by_regime = {}
    for name in REGIMES:
        mask = lab[name].to_numpy(bool)
        if mask.sum() < 20:
            continue
        rr, bb = run.ret[mask], run.bench[mask]
        by_regime[name] = {
            "days": int(mask.sum()),
            "atlas_ann": float(np.expm1(np.log1p(rr).mean() * 252)),
            "spy_ann": float(np.expm1(np.log1p(bb).mean() * 252)),
            "exposure": float(run.exposure[mask].mean()),
        }
    best, worst = _periods(run.dates, run.ret, run.bench)
    lose, win = _streaks(run.dates, run.ret, run.bench)
    a = Autopsy(
        excess_by_year={int(k): float(v) for k, v in M.calendar_excess(run.dates, run.ret, run.bench).items()},
        best_periods=best, worst_periods=worst,
        max_dd=_max_dd(run.dates, run.ret, run.exposure), by_regime=by_regime,
        longest_losing_months=lose, longest_winning_months=win,
    )
    a.tags = diagnose(a, run)
    return a


def diagnose(a: Autopsy, run: Run) -> list:
    """Failure tags, worst first. Plain rules, written down so they can be argued with."""
    tags = []
    s = M.stats(run.ret, run.cash)
    spy = M.stats(run.bench, run.cash)
    reg = a.by_regime
    if s.max_dd <= spy.max_dd * 0.8 and a.max_dd["mean_exposure_into_trough"] > 0.6:
        tags.append("crash_drawdown")          # stayed invested into the hole
    if "recovery" in reg and reg["recovery"]["atlas_ann"] < reg["recovery"]["spy_ann"] - 0.15:
        tags.append("missed_rebound")          # de-risked and came back late
    if "bull" in reg and reg["bull"]["atlas_ann"] < reg["bull"]["spy_ann"] - 0.05:
        tags.append("bull_lag")                # too little risk when it paid
    if run.exposure.mean() < 0.6:
        tags.append("too_defensive")
    years = len(run.ret) / 252
    if run.costs_paid / years > 0.004 or run.turnover / years > 8:
        tags.append("whipsaw")
    if s.cagr <= spy.cagr and not tags:
        tags.append("return_shortfall")
    return tags


def contribution(m: Market, g, start, end) -> dict:
    """What each layer added, by switching it off. CAGR and max DD deltas."""
    full = backtest(m, g, start, end)
    base = (M.cagr(full.ret), M.max_drawdown(full.ret))
    out = {}
    for layer in ("value", "lowvol", "trend_filter", "vol_target", "regime"):
        run = backtest(m, g, start, end, ab=Ablation(**{layer: False}))
        out[layer] = {"cagr_added": base[0] - M.cagr(run.ret),
                      "max_dd_improved": base[1] - M.max_drawdown(run.ret)}
    out["momentum_core"] = {"note": "the selection engine itself; cannot be switched off"}
    return out
