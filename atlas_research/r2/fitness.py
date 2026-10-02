"""R2 fitness: SPY is the hurdle, judged year by year (PREREG_R2.md)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..validation import metrics as M

CLIP = 0.15


def yearly(dates, r, b):
    df = pd.DataFrame({"r": r, "b": b}, index=dates)
    out = []
    for _, x in df.groupby(df.index.year):
        out.append((np.prod(1 + x.r.values) - 1, np.prod(1 + x.b.values) - 1,
                    M.max_drawdown(x.r.values), M.max_drawdown(x.b.values)))
    return np.array(out)


def capture(dates, r, b):
    df = pd.DataFrame({"r": r, "b": b}, index=dates)
    mo = df.groupby([df.index.year, df.index.month]).apply(
        lambda x: pd.Series({"r": np.prod(1 + x.r) - 1, "b": np.prod(1 + x.b) - 1}))
    up, dn = mo[mo.b > 0], mo[mo.b < 0]
    upc = up.r.mean() / up.b.mean() if len(up) else float("nan")
    dnc = dn.r.mean() / dn.b.mean() if len(dn) else float("nan")
    return float(upc), float(dnc)


@dataclass
class R2Verdict:
    cagr: float
    spy_cagr: float
    max_dd: float
    spy_max_dd: float
    sharpe: float
    spy_sharpe: float
    calmar: float
    spy_calmar: float
    yearly_excess: float
    yearly_dd_gain: float
    up_capture: float
    down_capture: float
    exposure: float
    trades_per_year: float
    score: float
    fitness: float

    @property
    def beats_return(self):
        return self.cagr > self.spy_cagr

    @property
    def beats_drawdown(self):
        return self.max_dd > self.spy_max_dd

    def row(self):
        d = dict(self.__dict__)
        d.update(beats_return=self.beats_return, beats_drawdown=self.beats_drawdown)
        return d


def judge(run) -> R2Verdict:
    s = M.stats(run.ret, run.cash)
    b = M.stats(run.bench, run.cash)
    y = yearly(run.dates, run.ret, run.bench)
    ex = float(np.clip(y[:, 0] - y[:, 1], -CLIP, CLIP).mean())
    ddg = float((np.abs(y[:, 3]) - np.abs(y[:, 2])).mean())
    upc, dnc = capture(run.dates, run.ret, run.bench)
    asym = (upc - dnc) if math.isfinite(upc) and math.isfinite(dnc) else 0.0
    score = (0.45 * math.tanh(ex / 0.03) + 0.25 * math.tanh(ddg / 0.05)
             + 0.15 * math.tanh(asym / 0.3) + 0.075 * math.tanh((s.sharpe - b.sharpe) / 0.5)
             + 0.075 * math.tanh((s.calmar - b.calmar) / 0.3))
    years = len(run.ret) / 252
    expo = float(run.exposure.mean())
    tpy = run.trades / years
    fails = int(expo < 0.5) + int(tpy < 4)
    return R2Verdict(cagr=s.cagr, spy_cagr=b.cagr, max_dd=s.max_dd, spy_max_dd=b.max_dd,
                     sharpe=s.sharpe, spy_sharpe=b.sharpe, calmar=s.calmar, spy_calmar=b.calmar,
                     yearly_excess=ex, yearly_dd_gain=ddg, up_capture=upc, down_capture=dnc,
                     exposure=expo, trades_per_year=tpy, score=score, fitness=score - 2 * fails)
