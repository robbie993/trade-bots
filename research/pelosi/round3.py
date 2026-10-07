"""Round 3: public tests of how an edge could reach her (or any member).

1. Earnings foreknowledge: did her buys come before earnings beats and good
   reactions, and her sales before misses, more than the same stocks' base rate?
2. Leak signature: abnormal return in the 1, 5 and 20 days after her trade
   date vs the same stock on random days (news-driven trades show a jump).
3. Pre-trade run-up and volume on her trade day vs normal.
4. Owner: spouse vs self vs joint vs child trades, congress-wide.
5. Leaders (Pelosi, Clark, Jeffries, Boehner) vs all other members.
Writes out/round3.json."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import Prices, cluster_boot
from ledger import build

warnings.filterwarnings("ignore")
D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"
P = Prices()
led = build()
rng = np.random.default_rng(0)
R = {}


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def box(x, cl=None):
    x = pd.Series(x).dropna()
    if len(x) < 3:
        return dict(n=int(len(x)))
    if cl is not None:
        m, lo, hi = cluster_boot(x, pd.Series(cl).loc[x.index])
    else:
        m, lo, hi = cluster_boot(x, pd.Series(range(len(x)), index=x.index))
    return dict(n=int(len(x)), mean=pct(m), lo=pct(lo), hi=pct(hi), hit=pct((x > 0).mean()))


trades = led[led.kind.isin(["buy_stock", "buy_call", "sell_stock", "sell_call"])].copy()
trades["side"] = np.where(trades.kind.str.startswith("buy"), "buy", "sell")

# ---------------------------------------------------------------- 1. earnings
E = pd.read_csv(D / "pelosi_earnings.csv")
E["d"] = pd.to_datetime(E["Earnings Date"].str[:10])
E = E[E["Reported EPS"].notna()].copy()
E["ticker"] = E.ticker.replace({"FB": "META", "SQ": "XYZ"})


def reaction(t, d):
    """Stock minus SPY from the close before the report to the close after."""
    if t not in P.adj:
        return np.nan
    i = P.cal.searchsorted(d)            # first session on/after the report date
    if i < 1 or i + 1 >= len(P.cal):
        return np.nan
    a, s = P.adj[t].values, P.adj["SPY"].values
    j0, j1 = i - 1, i + 1                 # covers before-open and after-close reports
    if not (np.isfinite(a[j0]) and np.isfinite(a[j1])):
        return np.nan
    return a[j1] / a[j0] - s[j1] / s[j0]


E["react"] = [reaction(t, d) for t, d in zip(E.ticker, E.d)]
E = E[(E.d >= "2013-06-01")]
base = dict(n=len(E), beat=pct((E["Surprise(%)"] > 0).mean()), react_mean=pct(E.react.mean()),
            react_pos=pct((E.react > 0).mean()))
earn = {"all reports, her tickers 2013-2026 (base rate)": base}
for side in ("buy", "sell"):
    rows = []
    for _, tr in trades[trades.side == side].iterrows():
        nx = E[(E.ticker == tr.px_ticker) & (E.d > tr.tdate) & (E.d <= tr.tdate + pd.Timedelta(days=90))]
        if len(nx):
            r = nx.sort_values("d").iloc[0]
            rows.append(dict(t=tr.px_ticker, gap=(r.d - tr.tdate).days, beat=r["Surprise(%)"] > 0,
                             surprise=r["Surprise(%)"], react=r.react))
    X = pd.DataFrame(rows)
    # base rate for the same tickers only
    bt = E[E.ticker.isin(X.t)] if len(X) else E.iloc[:0]
    earn[f"next report after her {side}s (within 90 days)"] = dict(
        n=len(X), beat=pct(X.beat.mean()), same_tickers_base_beat=pct((bt["Surprise(%)"] > 0).mean()),
        react_mean=pct(X.react.mean()), same_tickers_base_react=pct(bt.react.mean()),
        react_pos=pct((X.react > 0).mean()), median_days_before=float(X.gap.median()) if len(X) else None)
R["earnings_foreknowledge"] = earn

# ---------------------------------------------------------------- 2/3. leak signature
OFFS = np.r_[np.arange(-252, -30), np.arange(31, 253)]


def car(t, i, h):
    a, s = P.adj[t].values, P.adj["SPY"].values
    if i < 1 or i + h >= len(a) or not (np.isfinite(a[i - 1]) and np.isfinite(a[i + h])):
        return np.nan
    # from the close before her trade day: what an insider would capture
    return a[i + h] / a[i - 1] - s[i + h] / s[i - 1]


def pre(t, i, h=20):
    a, s = P.adj[t].values, P.adj["SPY"].values
    if i - h - 1 < 0 or i - 1 >= len(a) or not (np.isfinite(a[i - h - 1]) and np.isfinite(a[i - 1])):
        return np.nan
    return a[i - 1] / a[i - h - 1] - s[i - 1] / s[i - h - 1]


pv = pd.read_parquet(D / "pelosi_prices.parquet", columns=["date", "ticker", "volume"])
pv["ticker"] = pv.ticker.replace({"FB": "META_OLD"})
V = pv.pivot_table(index="date", columns="ticker", values="volume", aggfunc="last").reindex(P.cal)
leak = {}
for side in ("buy", "sell"):
    rows = []
    for _, tr in trades[trades.side == side].iterrows():
        t = tr.px_ticker
        i = P.idx_on_or_after(tr.tdate)
        if t not in P.adj or i is None or not P.has(t, i):
            continue
        r = dict(t=t, date=tr.tdate)
        for h in (1, 5, 20):
            r[f"car{h}"] = car(t, i, h)
            rnd = [car(t, i + o, h) for o in rng.choice(OFFS, 80)]
            r[f"rnd{h}"] = np.nanmean(rnd)
        r["pre20"] = pre(t, i)
        r["pre20_rnd"] = np.nanmean([pre(t, i + o) for o in rng.choice(OFFS, 80)])
        if t in V and i >= 61:
            v = V[t].values
            base_v = np.nanmean(v[i - 61:i - 1])
            r["vol_ratio"] = v[i] / base_v if base_v > 0 else np.nan
        rows.append(r)
    X = pd.DataFrame(rows)
    sign = 1 if side == "buy" else -1    # for sales, a fall afterwards is the "informed" direction
    leak[side] = {f"{h} day(s) after, her date minus random days (informed direction +)":
                  box(sign * (X[f"car{h}"] - X[f"rnd{h}"]), X.date) for h in (1, 5, 20)}
    leak[side]["raw after-return vs SPY"] = {h: pct((X[f"car{h}"]).mean()) for h in (1, 5, 20)}
    leak[side]["20 days before her trade vs SPY"] = dict(hers=pct(X.pre20.mean()), random_days=pct(X.pre20_rnd.mean()))
    if "vol_ratio" in X:
        leak[side]["volume on her trade day vs prior 60-day average"] = dict(
            median=round(float(X.vol_ratio.median()), 2), share_over_1_5x=pct((X.vol_ratio > 1.5).mean()))
R["leak_signature"] = leak

# ---------------------------------------------------------------- 4/5. congress-wide owner and leaders
px = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "adj_close"]) for f in sorted(D.glob("congress_prices_*.parquet"))])
C = px.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
lr = np.log(C / C.ffill().shift()).abs()
bad = set(lr.columns[(lr > np.log(3)).any()]) | {t for t in C.columns if re.fullmatch(r"[A-Z]{4}F", t)}
bad.discard("SPY")
C = C.drop(columns=sorted(bad))
cal, Cv, sv = C.index, C.to_numpy(), C["SPY"].ffill().to_numpy()
col = {t: k for k, t in enumerate(C.columns)}
h = pd.read_parquet(D / "congress_hf.parquet")
h = h[h.supersededAt.isna() & (h.action == "purchase") & h.ticker.notna()
      & (h.assetTypeCode.isna() | (h.assetTypeCode == "ST"))].copy()
h["pub"] = (h.firstAvailableAt - pd.Timedelta(hours=5)).dt.normalize()
h["td"] = pd.to_datetime(h.transactionDate, errors="coerce")
h = h[h.pub >= "2014-01-01"]
h["ticker"] = h.ticker.str.upper().str.strip().replace({"FB": "META", "SQ": "XYZ"})
LEAD = {"Nancy Pelosi", "Katherine M. Clark", "Hakeem S. Jeffries", "John A. Boehner"}
rows = []
for g in h.itertuples():
    k = col.get(g.ticker)
    if k is None:
        continue
    for tag, d in (("pub", g.pub), ("trade", g.td)):
        if pd.isna(d):
            continue
        i = cal.searchsorted(d, side="right" if tag == "pub" else "left")
        if i + 126 >= len(cal) or not (np.isfinite(Cv[i, k]) and np.isfinite(Cv[i + 126, k])):
            continue
        rows.append(dict(tag=tag, owner=g.owner, leader=g.displayName in LEAD, member=g.displayName, pub=g.pub,
                         x=Cv[i + 126, k] / Cv[i, k] - 1 - 0.001 - (sv[i + 126] / sv[i] - 1)))
Z = pd.DataFrame(rows)
R["owner_6m_vs_spy"] = {f"{o} ({tag} timing)": box(g.x, g.pub) for (tag, o), g in Z.groupby(["tag", "owner"])}
R["leaders_6m_vs_spy"] = {f"{'leaders' if ld else 'everyone else'} ({tag} timing)": box(g.x, g.pub)
                          for (tag, ld), g in Z.groupby(["tag", "leader"])}
R["leaders_each_6m_vs_spy"] = {f"{m} ({tag})": box(g.x, g.pub) for (tag, m), g in Z[Z.leader].groupby(["tag", "member"])}
json.dump(R, open(OUT / "round3.json", "w"), indent=1, default=str)
print(json.dumps(R, indent=1, default=str))
