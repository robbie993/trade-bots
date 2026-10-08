"""Most-traded rule re-run with delisted and renamed stocks added back
(PC pull: delisted/ from Alpaca 2016+, renamed tickers from Yahoo).

Same rules as mt_grid.py (63-day dollar volume rank, ETFs out, 10 bps, month-end
rebalance, T-bills when the filter is off). A stock that delists while held
earns nothing until the next month-end, then drops out (its last price stands
in for the takeover or delisting price). Compares the old survivor-only
universe with the corrected one. Delisted prices start in 2016, so names that
died in 2014-15 are still missing for those two years.
Writes out/survivor_fix.json."""
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
HERE = Path(__file__).parent
D, OUT, DL = HERE / "data", HERE / "out", HERE / "delisted"
SPL = {"2014-15": ("2014-12-01", "2015-12-31"), "train": ("2014-12-01", "2020-12-31"),
       "validate": ("2021-01-01", "2022-12-31"), "test": ("2023-01-01", "2026-12-31"),
       "2016-26": ("2016-01-01", "2026-12-31"), "all": ("2014-12-01", "2026-12-31")}

base = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "adj_close", "close", "volume"])
                  for f in sorted(D.glob("congress_prices_*.parquet"))])
base["date"] = pd.to_datetime(base.date)
base["dv"] = base.close * base.volume
ren = pd.read_parquet(DL / "renamed_prices.parquet", columns=["date", "ticker", "adj_close", "close", "volume"])
ren["date"] = pd.to_datetime(ren.date)
ren = ren[~ren.ticker.isin(base.ticker.unique())]
ren["dv"] = ren.close * ren.volume
dl = pd.read_parquet(DL / "delisted_prices_alpaca.parquet", columns=["date", "ticker", "adj_close", "volume"])
dl["date"] = pd.to_datetime(dl.date).dt.normalize()
dl = dl[~dl.ticker.isin(base.ticker.unique())]
dl["dv"] = dl.adj_close * dl.volume          # both split-adjusted by Alpaca
DUP = {"GOOG", "BRK-A", "BRK.A", "FOXA", "NWSA", "ANTM", "UTX", "DWDP", "VRX"}

h = pd.read_parquet(D / "congress_hf.parquet", columns=["ticker", "assetDescription"])
desc = h.dropna().groupby(h.ticker.str.upper()).assetDescription.agg(lambda s: " ".join(map(str, s.unique()[:3])))
FUND = {t for t, d in desc.items() if re.search(r"\bETF\b|SPDR|iShares|Trust|Fund|Index|Vanguard|Invesco|ProShares", d, re.I)}
FUND |= set("SPY QQQ IWM DIA TLT GLD XLK SMH VOO VTI EEM EFA HYG LQD XLF XLE IVV SLV USO TQQQ SQQQ VXX UVXY ARKK "
            "SOXL BIL SHV MTUM NANC KRUZ VEA VWO AGG BND XLV XLI XLY XLP XLU GDX KRE XBI IWD".split())
FUND -= {"TWX", "TWC"}      # Time Warner (Cable) are companies, not trusts
P = Prices()


def setup(px):
    A = px.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
    A = A[A.index.isin(base.date.unique())]
    DV = px.pivot_table(index="date", columns="ticker", values="dv", aggfunc="last").reindex(A.index)
    DV = DV.where(A.notna()).rolling(63, min_periods=40).mean()
    lr = np.log(A / A.ffill().shift()).abs()
    bad = set(lr.columns[(lr > np.log(3)).any()]) | {t for t in A.columns if re.fullmatch(r"[A-Z]{4}F", t)}
    stocks = [t for t in A.columns if t not in FUND | bad | DUP]
    return A, DV, stocks


def make_runner(A, DV, stocks):
    cash = P.ret["BIL"].reindex(A.index).fillna(0).values
    qqq = A["QQQ"].ffill()
    tr = (qqq > qqq.rolling(200).mean()).values
    S = A[stocks]
    R, Sv, DVv = S.pct_change(fill_method=None).values, S.values, DV[stocks].values
    vol63 = S.pct_change(fill_method=None).rolling(63, min_periods=40).std().values
    i0 = A.index.searchsorted(pd.Timestamp("2014-11-28"))
    me = set(A.index.get_indexer(A.resample("ME").last().index.map(lambda d: A.index[A.index <= d][-1])))
    picks = {}

    def run(n, weight, bear):
        nav = np.ones(len(A))
        w = np.zeros(len(stocks))
        for i in range(i0 + 1, len(A)):
            r = np.nan_to_num(R[i])
            g = (w * r).sum() + (1 - w.sum()) * cash[i]
            nav[i] = nav[i - 1] * (1 + g)
            w = w * (1 + r) / (1 + g)
            if i in me:
                new = np.zeros(len(stocks))
                if bear == "none" or tr[i]:
                    ok = np.where(np.isfinite(DVv[i]) & np.isfinite(Sv[i]))[0]
                    pk = ok[np.argsort(-DVv[i, ok])][:n]
                    picks.setdefault((n, A.index[i]), [stocks[k] for k in pk])
                    if weight == "equal":
                        new[pk] = 1 / n
                    else:
                        iv = 1 / vol63[i, pk]
                        iv = np.where(np.isfinite(iv), iv, np.nanmean(iv))
                        new[pk] = iv / iv.sum()
                nav[i] *= 1 - 0.001 * np.abs(new - w).sum()
                w = new
        return pd.Series(nav[i0:], index=A.index[i0:])
    return run, picks


def stats(nav, a, b):
    s = nav[(nav.index >= a) & (nav.index <= b)]
    s = s / s.iloc[0]
    y = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return dict(cagr=round(float(s.iloc[-1] ** (1 / y) - 1) * 100, 1),
                sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                maxdd=round(float((s / s.cummax() - 1).min()) * 100, 1))


res, navs = {}, {}
for label, px in (("survivors only (old)", base), ("with delisted + renamed (fixed)", pd.concat([base, ren, dl]))):
    A, DV, stocks = setup(px)
    run, picks = make_runner(A, DV, stocks)
    out = {}
    for n, wt, br in itertools.product([5, 10], ["equal", "invvol"], ["none", "QQQ 200d"]):
        nav = run(n, wt, br)
        navs[(label, n, wt, br)] = nav
        out[f"{n} {wt} {br}"] = {sp: stats(nav, a, b) for sp, (a, b) in SPL.items()}
    dead = {t for t in stocks if t in set(dl.ticker)}
    held = [(d, t) for (n, d), ts in picks.items() if n == 10 for t in ts if t in dead]
    out["_delisted_names_picked_in_top10_months"] = pd.Series([t for _, t in held]).value_counts().to_dict()
    out["_stocks_in_universe"] = len(stocks)
    res[label] = out
    if label.startswith("survivors"):
        A0 = A
qqq, spy = A0["QQQ"].ffill(), A0["SPY"].ffill()
i0 = A0.index.searchsorted(pd.Timestamp("2014-11-28"))
res["benchmarks"] = {k: {sp: stats(s[i0:] / s.iloc[i0], a, b) for sp, (a, b) in SPL.items()} for k, s in (("SPY", spy), ("QQQ", qqq))}
json.dump(res, open(OUT / "survivor_fix.json", "w"), indent=1, default=str)
for k in res["survivors only (old)"]:
    if k.startswith("_"):
        continue
    o, f = res["survivors only (old)"][k], res["with delisted + renamed (fixed)"][k]
    print(f"{k:22s} all {o['all']['cagr']:5.1f} -> {f['all']['cagr']:5.1f} | 16-26 {o['2016-26']['cagr']:5.1f} -> {f['2016-26']['cagr']:5.1f} "
          f"| val {o['validate']['cagr']:5.1f} -> {f['validate']['cagr']:5.1f} | test {o['test']['cagr']:5.1f} -> {f['test']['cagr']:5.1f} "
          f"| sh {o['all']['sharpe']} -> {f['all']['sharpe']} | dd {o['all']['maxdd']} -> {f['all']['maxdd']}")
print(res["with delisted + renamed (fixed)"]["_delisted_names_picked_in_top10_months"])
print(res["benchmarks"]["SPY"]["2016-26"], res["benchmarks"]["QQQ"]["2016-26"])
