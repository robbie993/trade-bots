"""Can public signals pick Pelosi's stocks before she buys them?

Rules choose 10 stocks at each month-end from public data only (prices,
volume, other members' disclosed buys), hold them for a month, 10 bps per
trade. Universe: the 30 most-traded stocks by trailing 63-day dollar volume
among everything any member traded (point in time; ETFs/funds removed).
Reported: hit rate (her later buy was already in the rule's holdings),
returns vs SPY/QQQ, and train/validate/test splits.
Writes out/precursors.json."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from ledger import build

warnings.filterwarnings("ignore")
D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"
SPL = {"train 2014-2020": ("2014-12-01", "2020-12-31"), "validate 2021-2022": ("2021-01-01", "2022-12-31"),
       "test 2023-2026": ("2023-01-01", "2026-12-31"), "all": ("2014-12-01", "2026-12-31")}


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 1)


px = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "adj_close", "close", "volume"])
                for f in sorted(D.glob("congress_prices_*.parquet"))])
A = px.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
DV = px.assign(dv=px.close * px.volume).pivot_table(index="date", columns="ticker", values="dv",
                                                     aggfunc="last").reindex(A.index)
DV = DV.rolling(63, min_periods=40).mean()
h = pd.read_parquet(D / "congress_hf.parquet")
h = h[h.supersededAt.isna()].copy()
desc = h.groupby(h.ticker.str.upper()).assetDescription.agg(lambda s: " ".join(map(str, s.dropna().unique()[:3])))
fund = {t for t, d in desc.items() if re.search(r"\bETF\b|SPDR|iShares|Trust|Fund|Index|Vanguard|Invesco|ProShares", d, re.I)}
fund |= {"SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLK", "SMH", "VOO", "VTI", "EEM", "EFA", "HYG", "LQD",
         "XLF", "XLE", "IVV", "SLV", "USO", "TQQQ", "SQQQ", "VXX", "UVXY", "ARKK", "SOXL", "BIL", "SHV", "MTUM",
         "NANC", "KRUZ", "VEA", "VWO", "AGG", "BND", "XLV", "XLI", "XLY", "XLP", "XLU", "GDX", "KRE", "XBI"}
# same data-quality screen as congress_full.py
lr = np.log(A / A.ffill().shift()).abs()
bad = set(lr.columns[(lr > np.log(3)).any()]) | {t for t in A.columns if re.fullmatch(r"[A-Z]{4}F", t)}
# one line per company (second share classes out)
dup = {"GOOG", "BRK-A", "BRK.A", "FOXA", "NWSA", "UA", "LBRDA", "ZG"}
stocks = [t for t in A.columns if t not in fund and t not in bad and t not in dup]
R = A.pct_change()
spy, qqq = A["SPY"].ffill(), A["QQQ"].ffill()

# other members' buys, by the date they became public
h["pub"] = (h.firstAvailableAt - pd.Timedelta(hours=5)).dt.normalize()
ob = h[(h.action == "purchase") & h.ticker.notna() & ~h.filerLast.str.contains("Pelosi", na=False)
       & (h.assetTypeCode.isna() | h.assetTypeCode.isin(["ST", "OP"]))]
ob = ob.assign(ticker=ob.ticker.str.upper().replace({"FB": "META", "SQ": "XYZ"}))

ends = A.resample("ME").last().index
ends = [A.index[A.index <= d][-1] for d in ends if d >= pd.Timestamp("2014-11-01")]


def picks(rule, d):
    i = A.index.get_loc(d)
    dv = DV.loc[d, stocks].dropna()
    u = dv.sort_values(ascending=False).head(30).index
    a = A.iloc[i]
    mom12 = a[u] / A.iloc[max(i - 252, 0)][u] - 1 - (a[u] / A.iloc[max(i - 21, 0)][u] - 1)
    mom6 = a[u] / A.iloc[max(i - 126, 0)][u] - 1
    if rule == "Top 10 biggest (by trading volume)":
        return list(u[:10])
    if rule == "Top 30 biggest, 10 best 12-month momentum":
        return list(mom12.dropna().sort_values(ascending=False).head(10).index)
    if rule == "Top 30 biggest, 10 best 6-month momentum":
        return list(mom6.dropna().sort_values(ascending=False).head(10).index)
    if rule == "Dip in an uptrend (her playbook)":
        hi3 = A.iloc[max(i - 63, 0):i + 1][u].max()
        off = a[u] / hi3 - 1
        dip = off[(off <= -0.08) & (mom12 > 0)].index
        ranked = list(mom12[dip].sort_values(ascending=False).index)
        rest = [t for t in mom12.dropna().sort_values(ascending=False).index if t not in ranked]
        return (ranked + rest)[:10]
    if rule == "Most bought by other members (last 60 days)":
        w = ob[(ob.pub > d - pd.Timedelta(days=60)) & (ob.pub <= d)]
        big = set(dv.sort_values(ascending=False).head(100).index)
        c = w[w.ticker.isin(big)].groupby("ticker").size().sort_values(ascending=False)
        top = list(c.head(10).index)
        return top + [t for t in u if t not in top][: 10 - len(top)]
    raise ValueError(rule)


RULES = ["Top 10 biggest (by trading volume)", "Top 30 biggest, 10 best 12-month momentum",
         "Top 30 biggest, 10 best 6-month momentum", "Dip in an uptrend (her playbook)",
         "Most bought by other members (last 60 days)"]
hold = {r: {} for r in RULES}
navs = {}
for r in RULES:
    nav, cur, out = [1.0], [], [A.index.get_loc(ends[0])]
    for k, d in enumerate(ends[:-1]):
        new = picks(r, d)
        hold[r][d] = set(new)
        turn = len(set(new) ^ set(cur)) / max(len(new), 1) / 2 if cur else 1.0
        i0, i1 = A.index.get_loc(d), A.index.get_loc(ends[k + 1])
        seg = R.iloc[i0 + 1:i1 + 1][new].fillna(0).mean(axis=1)   # equal weight, daily rebalanced
        path = (1 + seg).cumprod() * nav[-1] * (1 - 0.001 * turn)
        nav.extend(path.values)
        out.extend(range(i0 + 1, i1 + 1))
        cur = new
    navs[r] = pd.Series(nav, index=A.index[out])
s0 = navs[RULES[0]].index[0]
navs["SPY"] = spy[spy.index >= s0] / spy[s0]
navs["QQQ"] = qqq[qqq.index >= s0] / qqq[s0]


def stats(nav, a, b):
    s = nav[(nav.index >= a) & (nav.index <= b)].ffill()
    s = s / s.iloc[0]
    y = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return dict(cagr=pct(s.iloc[-1] ** (1 / y) - 1), sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                maxdd=pct((s / s.cummax() - 1).min()))


res = {"universe": "top 30 by 63-day dollar volume among member-traded stocks (ETFs removed)",
       "rules": {}}
for k, nav in navs.items():
    res["rules"][k] = {sp: stats(nav, a, b) for sp, (a, b) in SPL.items()}

# hit rate: was her later buy already held at the month-end before her trade date?
led = build()
buys = led[led.kind.isin(["buy_stock", "buy_call"]) & (led.tdate >= "2014-12-31")]
hits = {}
for r in RULES:
    hh, n, in_u = 0, 0, 0
    for _, e in buys.iterrows():
        prev = [d for d in ends if d < e.tdate]
        if not prev:
            continue
        d = prev[-1]
        n += 1
        hh += e.px_ticker in hold[r].get(d, set())
    hits[r] = dict(n=n, already_held=pct(hh / n) if n else None)
# base rate: share of the 30-stock universe a rule holds (10/30), and her names' presence in the universe
inu = 0
for _, e in buys.iterrows():
    prev = [d for d in ends if d < e.tdate]
    if prev:
        dv = DV.loc[prev[-1], stocks].dropna().sort_values(ascending=False)
        inu += e.px_ticker in set(dv.head(30).index)
res["her buys already in the 30 biggest"] = pct(inu / len(buys))
res["hit_rate"] = hits
# other members bought the same stock (public) within 90 days before her trade
pre = []
for _, e in buys.iterrows():
    w = ob[(ob.ticker == e.px_ticker) & (ob.pub < e.tdate) & (ob.pub >= e.tdate - pd.Timedelta(days=90))]
    pre.append(len(w))
pre = np.array(pre)
res["other members bought it first (90 days)"] = dict(share=pct((pre > 0).mean()), median_count=float(np.median(pre)))
res["sample picks"] = {r: {str(d.date()): sorted(hold[r][d]) for d in list(hold[r])[::24]} for r in RULES}
json.dump(res, open(OUT / "precursors.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
