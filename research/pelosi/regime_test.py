"""Is the QQQ 200-day filter real, or picked because it fixed 2022? (GPT's next step)

Pre-registered before running (2026-10-08):
  Strategy fixed: top 5 / top 10 most-traded (63-day dollar volume, survivor-fixed
  universe from survivor_fix.py), equal weight, month-end rebalance, 10 bps.
  Filters, all checked at month end, cash (BIL) when off:
    none
    Faber 10-month SMA on SPY            (published 2007)
    Faber 10-month SMA on QQQ            (same rule, the index the stocks live in)
    Absolute momentum: SPY 12m > T-bills (Antonacci 2012)
    Absolute momentum: QQQ 12m > T-bills
    QQQ 200-day SMA                      (ours, chosen after seeing 2022: reference)
  Only the first five were published before 2022, so they can't have been
  chosen to fix it.
  Placebo: the QQQ 200-day on/off sequence shifted in time by every offset
  from 6 to 120 months (wrapping around). That keeps how often and how long it
  sits in cash, but breaks the link to the market. If QQQ 200-day timing is
  real, it should beat nearly all shifts in 2021-22 and overall.
Monthly returns, so numbers differ slightly from the daily runs.
Writes out/regime_test.json."""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd


warnings.filterwarnings("ignore")

_src = open(__file__.replace("regime_test.py", "survivor_fix.py")).read()
exec(_src[:_src.index("res, navs = {}, {}")])   # data loading and setup() from survivor_fix.py

A, DV, stocks = setup(pd.concat([base, ren, dl]))   # noqa: F821
cal = A.index
me = [cal[cal <= d][-1] for d in A.resample("ME").last().index]
me = [d for d in me if d >= pd.Timestamp("2014-11-28")]
S = A[stocks]
bil = P.ret["BIL"].reindex(cal).fillna(0)   # noqa: F821
spy, qqq = A["SPY"].ffill(), A["QQQ"].ffill()


def basket_month(i0, i1, picks):
    """Equal-weight buy at close i0, hold to close i1 (no rebalance inside)."""
    p0, p1 = S.loc[i0, picks], S.loc[:i1, picks].ffill().loc[i1]
    return float((p1 / p0).mean() - 1)


months, picks_by = [], {}
for k in range(len(me) - 1):
    d0, d1 = me[k], me[k + 1]
    dv = DV.loc[d0, stocks]
    ok = dv[np.isfinite(dv) & np.isfinite(S.loc[d0])]
    order = ok.sort_values(ascending=False).index
    row = {"start": d0, "end": d1, "cash": float((1 + bil[(cal > d0) & (cal <= d1)]).prod() - 1)}
    for n in (5, 10):
        pk = list(order[:n])
        picks_by[(n, d0)] = pk
        row[f"top{n}"] = basket_month(d0, d1, pk)
    months.append(row)
M = pd.DataFrame(months).set_index("end")


def sma_on(series, n):
    return (series > series.rolling(n).mean())


filters = {
    "none": pd.Series(True, index=cal),
    "Faber 10-month SPY": None, "Faber 10-month QQQ": None,
    "Abs momentum SPY 12m": None, "Abs momentum QQQ 12m": None,
    "QQQ 200-day (ours)": sma_on(qqq, 200),
}
all_me = [cal[cal <= d][-1] for d in A.resample("ME").last().index]   # last trading day of each month
mspy, mqqq = spy.loc[all_me], qqq.loc[all_me]
mbil = (1 + bil).cumprod().loc[all_me]
f_spy10 = (mspy > mspy.rolling(10).mean())
f_qqq10 = (mqqq > mqqq.rolling(10).mean())
f_am_spy = (mspy / mspy.shift(12) - 1) > (mbil / mbil.shift(12) - 1)
f_am_qqq = (mqqq / mqqq.shift(12) - 1) > (mbil / mbil.shift(12) - 1)
start_on = {}
for name, f in filters.items():
    if f is not None:
        start_on[name] = [bool(f.loc[d]) for d in M.start]
for name, f in (("Faber 10-month SPY", f_spy10), ("Faber 10-month QQQ", f_qqq10),
                ("Abs momentum SPY 12m", f_am_spy), ("Abs momentum QQQ 12m", f_am_qqq)):
    start_on[name] = [bool(f.loc[f.index <= d].iloc[-1]) for d in M.start]


def turnover(n, on):
    t, prev = [], set()
    for d, o in zip(M.start, on):
        cur = set(picks_by[(n, d)]) if o else set()
        changed = len(cur ^ prev) / n if (cur or prev) else 0
        t.append(changed)
        prev = cur
    return np.array(t)


def run(n, on):
    on = np.array(on, dtype=bool)
    r = np.where(on, M[f"top{n}"].values, M["cash"].values) - 0.001 * turnover(n, on)
    return pd.Series(r, index=M.index)


SPL = {"train 2015-20": ("2014-12-01", "2020-12-31"), "2021-22": ("2021-01-01", "2022-12-31"),
       "2023-26": ("2023-01-01", "2026-12-31"), "all": ("2014-12-01", "2026-12-31")}


def stats(r):
    out = {}
    for k, (a, b) in SPL.items():
        x = r[(r.index >= a) & (r.index <= b)]
        nav = (1 + x).cumprod()
        yrs = len(x) / 12
        out[k] = dict(cagr=round(float(nav.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
                      sharpe=round(float(x.mean() / x.std() * np.sqrt(12)), 2),
                      maxdd=round(float((nav / nav.cummax() - 1).min()) * 100, 1))
    return out


res = {"months": len(M), "filters": {}}
for n in (5, 10):
    for name, on in start_on.items():
        res["filters"][f"top{n} | {name}"] = dict(invested=round(float(np.mean(on)) * 100), **stats(run(n, on)))
bench = {}
for nm, s in (("SPY", spy), ("QQQ", qqq)):
    bench[nm] = stats(pd.Series([s.loc[e] / s.loc[b] - 1 for b, e in zip(M.start, M.index)], index=M.index))
res["benchmarks"] = bench

# placebo: shift the QQQ 200-day on/off sequence in time
base_on = np.array(start_on["QQQ 200-day (ours)"])
for n in (5,):
    real = stats(run(n, base_on))
    sh = []
    for off in range(6, len(base_on) - 6):
        st = stats(run(n, np.roll(base_on, off)))
        sh.append((st["2021-22"]["cagr"], st["all"]["cagr"], st["all"]["sharpe"]))
    sh = np.array(sh)
    res[f"placebo top{n}"] = dict(
        shifts=len(sh),
        real_2021_22=real["2021-22"]["cagr"], share_of_shifts_beaten_2021_22=round(float((sh[:, 0] < real["2021-22"]["cagr"]).mean()) * 100, 1),
        real_all=real["all"]["cagr"], share_of_shifts_beaten_all=round(float((sh[:, 1] < real["all"]["cagr"]).mean()) * 100, 1),
        real_sharpe=real["all"]["sharpe"], share_of_shifts_beaten_sharpe=round(float((sh[:, 2] < real["all"]["sharpe"]).mean()) * 100, 1),
        median_shift_2021_22=round(float(np.median(sh[:, 0])), 1), median_shift_all=round(float(np.median(sh[:, 1])), 1))
json.dump(res, open(OUT / "regime_test.json", "w"), indent=1, default=str)   # noqa: F821
for k, v in res["filters"].items():
    print(f"{k:34s} inv {v['invested']:3d}% | train {v['train 2015-20']['cagr']:5.1f} | 21-22 {v['2021-22']['cagr']:6.1f} | 23-26 {v['2023-26']['cagr']:5.1f} | all {v['all']['cagr']:5.1f} sh {v['all']['sharpe']} dd {v['all']['maxdd']}")
print(res["benchmarks"])
print(res["placebo top5"])
