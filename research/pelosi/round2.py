"""Round 2: where does the Pelosi copy actually beat SPY, and which copy rules
keep the most of it? Writes out/round2.json and out/round2_yearly.csv."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import (Prices, bench_nav, build_positions, cluster_boot, entry_index, metrics,
                    regress_alpha, simulate)
from ledger import build

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "out"
REAL = "Public, next day (realistic)"
T0 = "T+0 (trade date, diagnostic)"
P = Prices()
led = build()
R = {}


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 1)


def run(rule=REAL, **kw):
    sim_kw = {k: kw.pop(k) for k in ("stock_cost_bps",) if k in kw}
    pos, _ = build_positions(P, led, rule, **kw)
    s0 = min(p.i0 for p in pos)
    nav, ex, to = simulate(P, pos, s0, **sim_kw)
    return nav, ex, pos


VARIANTS = {
    "A. Copy, sell when she sells (baseline)": dict(),
    "B. Copy, never sell (keep her picks)": dict(exit_on_sale=False),
    "C. Copy, hold each buy 1 year": dict(exit_on_sale=False, hold_td=252),
    "D. Copy, hold each buy 2 years": dict(exit_on_sale=False, hold_td=504),
    "E. Copy, never sell, equal weight": dict(exit_on_sale=False, weight="equal"),
    "F. Her own timing (trade date, not copyable)": dict(rule=T0),
    "G. Her own timing with her options (not copyable)": dict(rule=T0, use_options=True),
    "H. Copy with her options (deep calls), sell when she sells": dict(use_options=True),
}
navs = {}
for name, kw in VARIANTS.items():
    navs[name] = run(**dict(kw))[0]
start = navs["A. Copy, sell when she sells (baseline)"].index[0]
i0 = P.cal.get_loc(start)
for b in ("SPY", "QQQ", "XLK", "TECH6"):
    navs[b] = bench_nav(P, b, i0)
# QQQ levered to the copy's beta (1.2x daily, financing at T-bill) as a fair "same risk" bar
q, rf = P.ret["QQQ"].iloc[i0 + 1:], P.ret["BIL"].iloc[i0 + 1:].fillna(0)
navs["QQQ x1.2 (same risk as copy)"] = pd.concat(
    [pd.Series([1.0], index=[start]), (1 + 1.2 * q - 0.2 * rf).cumprod()])

df = pd.DataFrame({k: v.reindex(P.cal[i0:]).ffill() for k, v in navs.items()})
spy = df["SPY"]


def windows(nav, years):
    n = int(252 * years)
    a = nav / nav.shift(n) - 1
    b = spy / spy.shift(n) - 1
    d = (a - b).dropna()
    q = df["QQQ"] / df["QQQ"].shift(n) - 1
    dq = (a - q).dropna()
    return dict(share_beating_SPY=pct((d > 0).mean()), share_beating_QQQ=pct((dq > 0).mean()),
                worst_vs_SPY=pct(d.min()), median_vs_SPY=pct(d.median()))


yr = df.resample("YE").last()
yr = (yr / yr.shift(1).fillna(1.0) - 1)
yr.index = yr.index.year
(yr * 100).round(1).to_csv(OUT / "round2_yearly.csv")
summary = {}
for k in df.columns:
    m = metrics(df[k], P)
    a = regress_alpha(df[k], P)
    full = [y for y in yr.index if y not in (yr.index[0], yr.index[-1])]   # full calendar years only
    beat = int((yr.loc[full, k] > yr.loc[full, "SPY"]).sum())
    summary[k] = dict(cagr=pct(m["cagr"]), sharpe=round(m["sharpe"], 2), maxdd=pct(m["maxdd"]),
                      years_beating_SPY=f"{beat} of {len(full)}",
                      years_beating_QQQ=f"{int((yr.loc[full, k] > yr.loc[full, 'QQQ']).sum())} of {len(full)}",
                      roll3y=windows(df[k], 3), roll5y=windows(df[k], 5),
                      alpha3=pct(a["alpha_ann"]), alpha3_t=round(a["alpha_t"], 2))
R["variants"] = summary
R["yearly"] = {int(y): {k: pct(v) for k, v in r.items()} for y, r in yr.iterrows()}

# By era: Speaker (2019-01-03 to 2023-01-03), Leader before, after leaving leadership
ERAS = {"Minority Leader 2014-2018": ("2014-12-01", "2019-01-02"), "Speaker 2019-2022": ("2019-01-03", "2023-01-03"),
        "Out of leadership 2023-2026": ("2023-01-04", "2026-12-31")}
era = {}
for e, (a, b) in ERAS.items():
    seg = df[(df.index >= a) & (df.index <= b)]
    seg = seg / seg.iloc[0]
    yrs = (seg.index[-1] - seg.index[0]).days / 365.25
    era[e] = {k: pct(seg[k].iloc[-1] ** (1 / yrs) - 1) for k in
              ("A. Copy, sell when she sells (baseline)", "B. Copy, never sell (keep her picks)",
               "F. Her own timing (trade date, not copyable)", "SPY", "QQQ")}
R["eras_cagr"] = era

# ---------------------------------------------------------------- timing vs selection
# For each buy: 1-year return vs SPY when copied, against the same stock bought on
# random other days 1-12 months before or after (same stock, no timing).
rng = np.random.default_rng(0)
buys = led[led.kind.isin(["buy_stock", "buy_call"])]
rows = []
adj = P.adj
for _, e in buys.iterrows():
    for rule, tag in ((REAL, "copy"), (T0, "her")):
        i = entry_index(P, e, rule)
        t = e.px_ticker
        if i is None or i + 252 >= len(P.cal) or not P.has(t, i) or not np.isfinite(adj[t].iloc[i + 252]):
            continue
        x = adj[t].iloc[i + 252] / adj[t].iloc[i] - adj["SPY"].iloc[i + 252] / adj["SPY"].iloc[i]
        offs = np.r_[np.arange(-252, -21), np.arange(22, 253)]
        rnd = []
        for o in rng.choice(offs, 60):
            j = i + o
            if 0 <= j and j + 252 < len(P.cal) and np.isfinite(adj[t].iloc[j]) and np.isfinite(adj[t].iloc[j + 252]):
                rnd.append(adj[t].iloc[j + 252] / adj[t].iloc[j] - adj["SPY"].iloc[j + 252] / adj["SPY"].iloc[j])
        if len(rnd) >= 20:
            rows.append(dict(tag=tag, date=P.cal[i], ticker=t, x=x, rnd=float(np.mean(rnd)), pub=e.public_date))
TV = pd.DataFrame(rows)
tv = {}
for tag in ("copy", "her"):
    d = TV[TV.tag == tag]
    m, lo, hi = cluster_boot(d.x - d.rnd, d.pub)
    tv[tag] = dict(n=len(d), her_pick_vs_SPY=pct(d.x.mean()), same_stock_random_day_vs_SPY=pct(d.rnd.mean()),
                   timing_edge=pct(m), timing_ci=[pct(lo), pct(hi)],
                   timing_edge_by_era={e: pct((d.x - d.rnd)[(d.date >= a) & (d.date <= b)].mean())
                                       for e, (a, b) in ERAS.items()})
R["timing_vs_selection"] = tv
# Selection: her stock vs the mega-cap tech basket over the same year (copy timing)
d = TV[TV.tag == "copy"].copy()
d["tech"] = [P.adj["TECH6"].iloc[P.cal.get_loc(t) + 252] / P.adj["TECH6"].iloc[P.cal.get_loc(t)]
             - P.adj["SPY"].iloc[P.cal.get_loc(t) + 252] / P.adj["SPY"].iloc[P.cal.get_loc(t)] for t in d.date]
m, lo, hi = cluster_boot(d.x - d.tech, d.pub)
R["selection_vs_tech_basket"] = dict(n=len(d), mean=pct(m), ci=[pct(lo), pct(hi)],
                                     hit=pct(((d.x - d.tech) > 0).mean()))
json.dump(R, open(OUT / "round2.json", "w"), indent=1, default=str)
print(json.dumps(R, indent=1, default=str))
