"""Most-traded strategy grid (GPT's follow-up plan), price/volume variants.

Pre-registered grid (fixed before looking at results):
  N stocks        5, 10, 20, 30
  signal          size (most traded), 12-1 momentum, 6-month momentum,
                  low volatility, size+momentum rank blend
  pool            signals other than size pick from the 50 most traded
  weights         equal, inverse volatility
  rebalance       monthly, weekly
  bear filter     none, SPY above 200-day average, QQQ above 200-day average
                  (when off: all in T-bills)
Selection: best Sharpe on train 2014-12..2020-12 only. Then validate 2021-22
(2022 stays in) and test 2023-26. 10 bps per unit of turnover.
Writes out/mt_grid.json and out/mt_grid_all.csv."""
from __future__ import annotations

import itertools
import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import Prices

warnings.filterwarnings("ignore")
D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"
SPL = {"train": ("2014-12-01", "2020-12-31"), "validate": ("2021-01-01", "2022-12-31"),
       "test": ("2023-01-01", "2026-12-31"), "all": ("2014-12-01", "2026-12-31")}

px = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "adj_close", "close", "volume"])
                for f in sorted(D.glob("congress_prices_*.parquet"))])
A = px.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
DV = px.assign(dv=px.close * px.volume).pivot_table(index="date", columns="ticker", values="dv",
                                                     aggfunc="last").reindex(A.index).rolling(63, min_periods=40).mean()
h = pd.read_parquet(D / "congress_hf.parquet", columns=["ticker", "assetDescription"])
desc = h.dropna().groupby(h.ticker.str.upper()).assetDescription.agg(lambda s: " ".join(map(str, s.unique()[:3])))
fund = {t for t, d in desc.items() if re.search(r"\bETF\b|SPDR|iShares|Trust|Fund|Index|Vanguard|Invesco|ProShares", d, re.I)}
fund |= set("SPY QQQ IWM DIA TLT GLD XLK SMH VOO VTI EEM EFA HYG LQD XLF XLE IVV SLV USO TQQQ SQQQ VXX UVXY ARKK "
            "SOXL BIL SHV MTUM NANC KRUZ VEA VWO AGG BND XLV XLI XLY XLP XLU GDX KRE XBI".split())
lr = np.log(A / A.ffill().shift()).abs()
bad = set(lr.columns[(lr > np.log(3)).any()]) | {t for t in A.columns if re.fullmatch(r"[A-Z]{4}F", t)}
dup = {"GOOG", "BRK-A", "BRK.A", "FOXA", "NWSA"}
stocks = [t for t in A.columns if t not in fund | bad | dup]
P = Prices()
cash = P.ret["BIL"].reindex(A.index).fillna(0).values
spy, qqq = A["SPY"].ffill(), A["QQQ"].ffill()
trend = {"none": None, "SPY 200d": (spy > spy.rolling(200).mean()).values,
         "QQQ 200d": (qqq > qqq.rolling(200).mean()).values}
S = A[stocks]
R = S.pct_change().values
Sv, DVv = S.values, DV[stocks].values
vol63 = S.pct_change().rolling(63).std().values
cols = np.array(stocks)
i_start = A.index.searchsorted(pd.Timestamp("2014-11-28"))
month_ends = set(A.index.get_indexer(A.resample("ME").last().index.map(lambda d: A.index[A.index <= d][-1])))
weekly = set(range(i_start, len(A), 5))


def choose(i, n, signal):
    dv = DVv[i]
    ok = np.isfinite(dv) & np.isfinite(Sv[i])
    idx = np.where(ok)[0]
    order = idx[np.argsort(-dv[idx])]
    if signal == "size":
        return order[:n]
    pool = order[:50]
    p0, p252, p21, p126 = Sv[i, pool], Sv[max(i - 252, 0), pool], Sv[max(i - 21, 0), pool], Sv[max(i - 126, 0), pool]
    if signal == "mom12_1":
        sc = p21 / p252 - 1
    elif signal == "mom6":
        sc = p0 / p126 - 1
    elif signal == "lowvol":
        sc = -vol63[i, pool]
    elif signal == "size+mom":
        m = p21 / p252 - 1
        sc = -(np.argsort(np.argsort(-np.nan_to_num(m, nan=-9))) + np.arange(len(pool)))
    sc = np.where(np.isfinite(sc), sc, -np.inf)
    return pool[np.argsort(-sc)[:n]]


def run(n, signal, weight, rebal, bear):
    days = month_ends if rebal == "monthly" else weekly
    nav = np.ones(len(A))
    w = np.zeros(len(stocks))
    invested = False
    tr = trend[bear]
    for i in range(i_start + 1, len(A)):
        r = np.nan_to_num(R[i])
        g = (w * r).sum() + (1 - w.sum()) * cash[i]
        nav[i] = nav[i - 1] * (1 + g)
        w = w * (1 + r) / (1 + g) if (1 + g) != 0 else w
        if i in days:
            on = True if tr is None else bool(tr[i])
            new = np.zeros(len(stocks))
            if on:
                pk = choose(i, n, signal)
                if weight == "equal":
                    new[pk] = 1 / len(pk)
                else:
                    iv = 1 / np.where(np.isfinite(vol63[i, pk]) & (vol63[i, pk] > 0), vol63[i, pk], np.nan)
                    iv = np.nan_to_num(iv, nan=np.nanmean(iv))
                    new[pk] = iv / iv.sum()
            nav[i] *= 1 - 0.001 * np.abs(new - w).sum()
            w = new
            invested = on
    return pd.Series(nav[i_start:], index=A.index[i_start:]), invested


def stats(nav, a, b):
    s = nav[(nav.index >= a) & (nav.index <= b)]
    s = s / s.iloc[0]
    y = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return dict(cagr=round(float(s.iloc[-1] ** (1 / y) - 1) * 100, 1),
                sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                maxdd=round(float((s / s.cummax() - 1).min()) * 100, 1))


GRID = list(itertools.product([5, 10, 20, 30], ["size", "mom12_1", "mom6", "lowvol", "size+mom"],
                              ["equal", "invvol"], ["monthly", "weekly"], ["none", "SPY 200d", "QQQ 200d"]))
rows = []
for k, (n, sig, wt, rb, br) in enumerate(GRID):
    nav, _ = run(n, sig, wt, rb, br)
    row = dict(n=n, signal=sig, weight=wt, rebalance=rb, bear=br)
    for sp, (a, b) in SPL.items():
        for m, v in stats(nav, a, b).items():
            row[f"{sp}_{m}"] = v
    rows.append(row)
    if k % 40 == 0:
        print(k, row, flush=True)
G = pd.DataFrame(rows)
G.to_csv(OUT / "mt_grid_all.csv", index=False)
bench = {}
for name, s in (("SPY", spy), ("QQQ", qqq)):
    s = s[s.index >= A.index[i_start]]
    bench[name] = {sp: stats(s / s.iloc[0], a, b) for sp, (a, b) in SPL.items()}
best = G.sort_values("train_sharpe", ascending=False).iloc[0].to_dict()
top10 = G.sort_values("train_sharpe", ascending=False).head(10)
res = dict(variants=len(G), benchmarks=bench, train_pick=best,
           train_top10_validate_mean=dict(cagr=round(top10.validate_cagr.mean(), 1), sharpe=round(top10.validate_sharpe.mean(), 2)),
           train_top10_test_mean=dict(cagr=round(top10.test_cagr.mean(), 1), sharpe=round(top10.test_sharpe.mean(), 2)),
           share_beating_SPY_in_validate=round(float((G.validate_cagr > bench["SPY"]["validate"]["cagr"]).mean()) * 100, 1),
           share_beating_SPY_in_test=round(float((G.test_cagr > bench["SPY"]["test"]["cagr"]).mean()) * 100, 1),
           best_validate_only_bear_filters=G[G.bear != "none"].sort_values("train_sharpe", ascending=False).head(3).to_dict("records"),
           plain_10_most_traded=G[(G.n == 10) & (G.signal == "size") & (G.weight == "equal") & (G.rebalance == "monthly")].to_dict("records"))
# by factor: average across the grid (what each choice does, all else varied)
res["by_choice"] = {c: G.groupby(c)[["train_sharpe", "validate_cagr", "test_cagr", "all_cagr", "all_maxdd", "all_sharpe"]].mean().round(2).to_dict("index")
                    for c in ("n", "signal", "weight", "rebalance", "bear")}
json.dump(res, open(OUT / "mt_grid.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k != "by_choice"}, indent=1, default=str))
