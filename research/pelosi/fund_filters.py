"""Most-traded strategy with valuation and earnings filters (GPT steps 3-4).

Pre-registered grid, fixed before looking at results:
  pool      the 20 or 30 most traded stocks (63-day dollar volume, ETFs out)
  N         5 or 10 held, equal weight, monthly
  signal    size        most traded (baseline)
            profitable  most traded among those with positive trailing EPS
            value       lowest trailing P/E (positive EPS only)
            growth      highest trailing-EPS growth vs a year earlier
            surprise    highest average EPS surprise over the last 2 reports
            beat        most traded among those that beat estimates last report
            garp        rank sum of value and growth
  filter    none, QQQ above its 200-day average (else T-bills)
Point in time: only earnings reported before the rebalance day count
(yfinance dates, split-adjusted EPS). Selection by train Sharpe 2014-12..2020;
validate 2021-22, test 2023-26. 10 bps per unit of turnover.
Same survivor-tilted universe as mt_grid.py (congress-traded tickers).
Writes out/fund_filters.json and out/fund_filters_all.csv."""
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


# ---- point-in-time earnings: trailing 4 reported EPS, growth, surprise
E = pd.read_csv(Path(__file__).parent / "fundamentals" / "earnings_all.csv")
E["d"] = pd.to_datetime(E["Earnings Date"].str[:10])
E = E.dropna(subset=["Reported EPS"]).sort_values(["ticker", "d"])
E["ttm"] = E.groupby("ticker")["Reported EPS"].transform(lambda s: s.rolling(4).sum())
E["ttm_prev"] = E.groupby("ticker")["ttm"].shift(4)
E["sur2"] = E.groupby("ticker")["Surprise(%)"].transform(lambda s: s.rolling(2).mean())
E["beat"] = (E["Reported EPS"] > E["EPS Estimate"]).astype(float)
E.loc[E["EPS Estimate"].isna(), "beat"] = np.nan
ix = {t: k for k, t in enumerate(cols)}
shape = (len(A), len(cols))
TTM, GROW, SUR, BEAT = (np.full(shape, np.nan) for _ in range(4))
for t, g in E[E.ticker.isin(ix)].groupby("ticker"):
    # an after-close report is first usable on the next session: available from d+1
    pos = A.index.searchsorted(g.d.values + np.timedelta64(1, "D"))
    for arr, col in ((TTM, "ttm"), (SUR, "sur2"), (BEAT, "beat")):
        s = np.full(len(A), np.nan)
        for p, v in zip(pos, g[col].values):
            if p < len(A):
                s[p:] = v
        arr[:, ix[t]] = s
    s = np.full(len(A), np.nan)
    for p, a_, b_ in zip(pos, g.ttm.values, g.ttm_prev.values):
        if p < len(A):
            s[p:] = (a_ - b_) / abs(b_) if np.isfinite(b_) and b_ > 0 else np.nan
    GROW[:, ix[t]] = s
CLOSE = px.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").reindex(A.index)[stocks].ffill().values
PE = CLOSE / TTM
coverage = float(np.isfinite(TTM[i_start:]).mean())


def rank(x):
    x = np.where(np.isfinite(x), x, np.nan)
    return pd.Series(x).rank(ascending=False).values


def choose(i, n, signal, pool_n):
    dv = DVv[i]
    ok = np.isfinite(dv) & np.isfinite(Sv[i])
    idx = np.where(ok)[0]
    pool = idx[np.argsort(-dv[idx])][:pool_n]
    if signal == "size":
        return pool[:n]
    eps, pe, gr, su, bt = TTM[i, pool], PE[i, pool], GROW[i, pool], SUR[i, pool], BEAT[i, pool]
    if signal == "profitable":
        keep = pool[np.nan_to_num(eps, nan=-1) > 0]
    elif signal == "beat":
        keep = pool[np.nan_to_num(bt, nan=0) > 0]
    elif signal == "value":
        sc = np.where((eps > 0) & np.isfinite(pe), -pe, -np.inf)
        keep = pool[np.argsort(-sc)][np.sort(-sc) < np.inf]
    elif signal == "growth":
        sc = np.where(np.isfinite(gr), gr, -np.inf)
        keep = pool[np.argsort(-sc)][np.sort(-sc) < np.inf]
    elif signal == "surprise":
        sc = np.where(np.isfinite(su), su, -np.inf)
        keep = pool[np.argsort(-sc)][np.sort(-sc) < np.inf]
    elif signal == "garp":
        v = np.where((eps > 0) & np.isfinite(pe), -pe, np.nan)
        sc = -(np.nan_to_num(rank(v), nan=99) + np.nan_to_num(rank(gr), nan=99))
        keep = pool[np.argsort(-sc)]
    # fill from the pool by size if the filter leaves fewer than n
    keep = list(keep[:n])
    for p in pool:
        if len(keep) >= n:
            break
        if p not in keep:
            keep.append(p)
    return np.array(keep)


def run(n, signal, pool_n, bear):
    nav = np.ones(len(A))
    w = np.zeros(len(stocks))
    tr = trend[bear]
    turn = 0.0
    for i in range(i_start + 1, len(A)):
        r = np.nan_to_num(R[i])
        g = (w * r).sum() + (1 - w.sum()) * cash[i]
        nav[i] = nav[i - 1] * (1 + g)
        w = w * (1 + r) / (1 + g) if (1 + g) != 0 else w
        if i in month_ends:
            new = np.zeros(len(stocks))
            if tr is None or bool(tr[i]):
                pk = choose(i, n, signal, pool_n)
                new[pk] = 1 / len(pk)
            turn += np.abs(new - w).sum()
            nav[i] *= 1 - 0.001 * np.abs(new - w).sum()
            w = new
    return pd.Series(nav[i_start:], index=A.index[i_start:]), turn


def stats(nav, a, b):
    s = nav[(nav.index >= a) & (nav.index <= b)]
    s = s / s.iloc[0]
    y = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return dict(cagr=round(float(s.iloc[-1] ** (1 / y) - 1) * 100, 1),
                sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                maxdd=round(float((s / s.cummax() - 1).min()) * 100, 1))


GRID = list(itertools.product([5, 10], ["size", "profitable", "value", "growth", "surprise", "beat", "garp"],
                              [20, 30], ["none", "QQQ 200d"]))
rows = []
for n, sig, pn, br in GRID:
    nav, turn = run(n, sig, pn, br)
    row = dict(n=n, signal=sig, pool=pn, bear=br, turnover_per_yr=round(turn / 11.9, 2))
    for sp, (a, b) in SPL.items():
        for m, v in stats(nav, a, b).items():
            row[f"{sp}_{m}"] = v
    rows.append(row)
G = pd.DataFrame(rows)
G.to_csv(OUT / "fund_filters_all.csv", index=False)
bench = {}
for name, s in (("SPY", spy), ("QQQ", qqq)):
    s = s[s.index >= A.index[i_start]]
    bench[name] = {sp: stats(s / s.iloc[0], a, b) for sp, (a, b) in SPL.items()}
show = ["n", "signal", "pool", "bear", "train_sharpe", "validate_cagr", "test_cagr", "all_cagr", "all_sharpe", "all_maxdd"]
res = dict(variants=len(G), eps_coverage_share=round(coverage * 100, 1), benchmarks=bench,
           train_pick=G.sort_values("train_sharpe", ascending=False).iloc[0].to_dict(),
           train_top5=G.sort_values("train_sharpe", ascending=False).head(5)[show].to_dict("records"),
           by_signal=G.groupby(["signal", "bear"])[["train_sharpe", "validate_cagr", "test_cagr", "all_cagr", "all_sharpe", "all_maxdd"]].mean().round(2).reset_index().to_dict("records"),
           vs_size_same_settings={})
base = G[G.signal == "size"].set_index(["n", "pool", "bear"])
for sig in G.signal.unique():
    if sig == "size":
        continue
    d = G[G.signal == sig].set_index(["n", "pool", "bear"])
    diff = (d[["train_cagr", "validate_cagr", "test_cagr", "all_cagr", "all_sharpe"]] - base[["train_cagr", "validate_cagr", "test_cagr", "all_cagr", "all_sharpe"]])
    res["vs_size_same_settings"][sig] = dict(mean=diff.mean().round(2).to_dict(),
                                             wins_all_cagr=f"{int((diff.all_cagr > 0).sum())} of {len(diff)}",
                                             wins_test_cagr=f"{int((diff.test_cagr > 0).sum())} of {len(diff)}")
json.dump(res, open(OUT / "fund_filters.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
