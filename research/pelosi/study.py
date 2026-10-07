"""Runs every test in the Project Pelosi research record and writes out/results.json
plus CSV tables. Usage: python study.py [path to old Alpaca prices.json for the
congress-wide test]."""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from engine import (RULES, Prices, bench_nav, build_positions, cluster_boot, entry_index,
                    event_returns, metrics, regress_alpha, simulate)
from ledger import build, load_raw

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
REAL = "Public, next day (realistic)"
TECH = {"AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "CRM", "NFLX", "PYPL", "AVGO",
        "PANW", "MU", "CRWD", "TSLA", "RBLX", "XYZ", "DBX", "WORK", "INTC", "UBER", "TEM", "V"}
R = {}


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def summarize(ev, col, cl="public_date"):
    ok = ev[ev.status == "ok"] if "status" in ev else ev
    m, lo, hi = cluster_boot(ok[col], ok[cl])
    x = ok[col].dropna()
    return dict(n=int(len(x)), mean=pct(m), lo=pct(lo), hi=pct(hi), median=pct(x.median()) if len(x) else None,
                hit=pct((x > 0).mean()) if len(x) else None)


def fmt_m(m):
    return {k: (pct(v) if k in ("total", "cagr", "vol", "maxdd", "alpha", "exposure") else
                (round(float(v), 3) if isinstance(v, (float, np.floating)) else v)) for k, v in m.items()}


P = Prices()
led = build()
raw = load_raw()
buys = led[led.kind.isin(["buy_stock", "buy_call"])].copy()
sells = led[led.kind.isin(["sell_stock", "sell_call"])].copy()
end_i = len(P.cal) - 1

# ------------------------------------------------------------ 1. ledger / reconciliation
clerk = pd.read_csv(Path(__file__).parent / "data" / "pelosi_clerk_index.csv")
R["ledger"] = dict(
    raw_rows=int(len(raw)), economic_events=int(len(led)), kinds=led.kind.value_counts().to_dict(),
    merged_duplicates=int((led.n_filings > 1).sum()), clerk_ptrs=int((clerk.FilingType == "P").sum()),
    hf_docs=int(raw.sourceDocId.nunique()),
    first_trade=str(led.tdate.min().date()), last_public=str(led.public_date.max().date()),
    lag_median=float(led.lag_days.median()), lag_p90=float(led.lag_days.quantile(.9)),
    lag_max=int(led.lag_days.max()),
    unpriced_buys=[f"{r.tdate.date()} {r.ticker} {r.kind}" for _, r in buys.iterrows()
                   if entry_index(P, r, REAL) is None or not P.has(r.px_ticker, entry_index(P, r, REAL))])
led.to_csv(OUT / "pelosi_ledger.csv", index=False)

# ------------------------------------------------------------ 2. delay ladder, event level
ladder = []
for rule in RULES:
    ev = event_returns(P, buys, rule, bench=("SPY", "QQQ", "TECH6"))
    row = {"rule": rule}
    for h in (21, 63, 126, 252):
        row[f"spy{h}"] = summarize(ev, f"x{h}_SPY")
        row[f"qqq{h}"] = summarize(ev, f"x{h}_QQQ")
        row[f"tech{h}"] = summarize(ev, f"x{h}_TECH6")
        row[f"raw{h}"] = summarize(ev, f"r{h}")
    ladder.append(row)
    if rule == REAL:
        ev.to_csv(OUT / "events_realistic.csv", index=False)
        ev_real = ev
R["event_ladder"] = ladder

# sells: did what she sold lag the market afterwards? (negative = good sell)
evs = event_returns(P, sells, REAL)
R["sells_after"] = {h: summarize(evs, f"x{h}_SPY") for h in (21, 63, 126, 252)}

# ------------------------------------------------------------ 3. copy portfolio ladder
def run_port(rule=REAL, opt=False, start=None, end=None, **kw):
    pos, sk = build_positions(P, led, rule, use_options=opt, end_i=end, **kw)
    if not pos:
        return None
    s0 = start if start is not None else min(p.i0 for p in pos)
    pos = [p for p in pos if p.i0 >= s0]
    nav, ex, to = simulate(P, pos, s0, end, **{k: v for k, v in kw.items() if k in ()})
    return nav, ex, to, pos, sk


port = []
navs = {}
for rule in RULES:
    for opt in (False, True):
        out = run_port(rule, opt)
        nav, ex, to, pos, sk = out
        m = fmt_m(metrics(nav, P, exposure=ex))
        a = regress_alpha(nav, P)
        port.append(dict(rule=rule, options=opt, positions=len(pos), skipped=len(sk), turnover=round(to, 2),
                         **m, alpha3=pct(a["alpha_ann"]), alpha3_t=round(a["alpha_t"], 2),
                         b_spy=round(a["b_SPY"], 2), b_qqq=round(a["b_QQQ"], 2), b_mtum=round(a["b_MTUM"], 2)))
        navs[(rule, opt)] = nav
R["portfolio_ladder"] = port

s0 = navs[(REAL, False)].index[0]
i0 = P.cal.get_loc(s0)
R["benchmarks"] = {b: fmt_m(metrics(bench_nav(P, b, i0), P)) for b in ("SPY", "QQQ", "XLK", "SMH", "MTUM", "TECH6")}
nav_real = navs[(REAL, False)]
pd.DataFrame({"copy_stock": nav_real, "copy_options": navs[(REAL, True)],
              "SPY": bench_nav(P, "SPY", i0), "QQQ": bench_nav(P, "QQQ", i0)}).to_csv(OUT / "nav_realistic.csv")

# Yearly table
yr = pd.DataFrame({"copy": nav_real, "SPY": bench_nav(P, "SPY", i0), "QQQ": bench_nav(P, "QQQ", i0),
                   "TECH6": bench_nav(P, "TECH6", i0)})
yr = yr.resample("YE").last()
yr = yr / yr.shift(1).fillna(1.0) - 1
R["yearly"] = {str(d.year): {k: pct(v) for k, v in r.items()} for d, r in yr.iterrows()}

# NANC head-to-head since NANC launched
n0 = P.idx_on_or_after("2023-02-08")
navn = run_port(REAL, False, start=n0)[0]
R["vs_nanc"] = {"copy": fmt_m(metrics(navn, P)), "NANC": fmt_m(metrics(bench_nav(P, "NANC", n0), P)),
                "SPY": fmt_m(metrics(bench_nav(P, "SPY", n0), P)), "QQQ": fmt_m(metrics(bench_nav(P, "QQQ", n0), P))}

# Win rate / profit factor of realistic stock positions
pos = run_port(REAL, False)[3]
rets = []
for p in pos:
    spy = P.adj["SPY"].iloc[p.i1] / P.adj["SPY"].iloc[p.i0] - 1
    rets.append(dict(ticker=p.ticker, entry=str(P.cal[p.i0].date()), exit=str(P.cal[p.i1].date()),
                     days=p.i1 - p.i0, usd=p.usd, ret=p.path[-1] - 1, spy=spy))
rets = pd.DataFrame(rets)
rets.to_csv(OUT / "positions_realistic.csv", index=False)
gain, loss = rets.ret[rets.ret > 0].sum(), -rets.ret[rets.ret < 0].sum()
R["positions"] = dict(n=len(rets), win=pct((rets.ret > 0).mean()), beat_spy=pct((rets.ret > rets.spy).mean()),
                      profit_factor=round(gain / loss, 2) if loss else None,
                      avg_hold_days=int(rets.days.mean()), median_hold_days=int(rets.days.median()))
contrib = (rets.usd / rets.usd.sum()) * (rets.ret - rets.spy)
R["top_contributors"] = rets.assign(c=contrib).sort_values("c", ascending=False).head(5)[
    ["ticker", "entry", "ret", "spy"]].round(3).to_dict("records")

# ------------------------------------------------------------ 4. filters / ranking / holding (walk-forward)
FILTERS = {
    "all buys": lambda d: d,
    "calls only": lambda d: d[d.kind == "buy_call"],
    "stock buys only": lambda d: d[d.kind == "buy_stock"],
    "big ($1M+)": lambda d: d[d.amountLow >= 1_000_001],
    "tech only": lambda d: d[d.px_ticker.isin(TECH)],
    "fresh (lag <= 20d)": lambda d: d[d.lag_days <= 20],
    "top half by $ each year": lambda d: d[d.mid_usd >= d.groupby(d.tdate.dt.year).mid_usd.transform("median")],
}
ev_all = event_returns(P, buys, REAL, horizons=(21, 63, 126, 252), bench=("SPY", "QQQ"))
ev_all = ev_all.merge(buys[["eventId", "amountLow", "px_ticker", "lag_days"]], on="eventId")
ev_all["entry"] = pd.to_datetime(ev_all["entry"])
SPLITS = {"train 2014-2020": ("2014-01-01", "2020-12-31"), "validate 2021-2022": ("2021-01-01", "2022-12-31"),
          "test 2023-2026": ("2023-01-01", "2026-12-31")}
grid = []
for fname, f in FILTERS.items():
    sub = f(ev_all)
    for h in (21, 63, 126, 252):
        row = dict(filter=fname, hold=h)
        for sname, (a, b) in SPLITS.items():
            part = sub[(sub.entry >= a) & (sub.entry <= b)]
            row[sname] = summarize(part, f"x{h}_SPY")
            row[sname + " vsQQQ"] = summarize(part, f"x{h}_QQQ")
        grid.append(row)
R["filter_grid"] = grid
cands = [g for g in grid if g["train 2014-2020"]["n"] >= 8]
best = max(cands, key=lambda g: g["train 2014-2020"]["mean"] or -1e9)
R["walk_forward_pick"] = dict(filter=best["filter"], hold=best["hold"],
                              train=best["train 2014-2020"], validate=best["validate 2021-2022"],
                              test=best["test 2023-2026"], test_vs_qqq=best["test 2023-2026 vsQQQ"])

# ------------------------------------------------------------ 5. stress tests (realistic stock copy)
stress = {}
base = metrics(nav_real, P)
stress["base"] = fmt_m(base)
pos_b, _ = build_positions(P, led, REAL)
nav2, *_ = simulate(P, pos_b, pos_b[0].i0 if pos_b else 0, stock_cost_bps=10.0)
stress["2x costs"] = fmt_m(metrics(nav2, P))
pos_d, _ = build_positions(P, led, REAL, extra_td=5)
stress["+5 trading days later"] = fmt_m(metrics(simulate(P, pos_d, min(p.i0 for p in pos_d))[0], P))
top3 = set(rets.assign(c=contrib).sort_values("c", ascending=False).head(3).index)
pos_x = [p for k, p in enumerate(pos_b) if k not in top3]
stress["without top 3 trades"] = fmt_m(metrics(simulate(P, pos_x, min(p.i0 for p in pos_x))[0], P))
pos_e = build_positions(P, led, REAL, weight="equal")[0]
stress["equal weight"] = fmt_m(metrics(simulate(P, pos_e, min(p.i0 for p in pos_e))[0], P))
for h in (63, 252):
    ph = build_positions(P, led, REAL, hold_td=h)[0]
    stress[f"fixed {h}-day hold"] = fmt_m(metrics(simulate(P, ph, min(p.i0 for p in ph))[0], P))
for name, (a, b) in {"2014-2019": ("2014-12-01", "2019-12-31"), "2020-2022": ("2020-01-01", "2022-12-31"),
                     "2023-2026": ("2023-01-01", "2026-12-31")}.items():
    ia, ib = P.idx_on_or_after(a), min(P.idx_on_or_after(b) or end_i, end_i)
    seg = nav_real[(nav_real.index >= P.cal[ia]) & (nav_real.index <= P.cal[ib])]
    seg = seg / seg.iloc[0]
    stress[f"period {name}"] = {"copy": fmt_m(metrics(seg, P)),
                                "SPY": fmt_m(metrics(bench_nav(P, "SPY", P.cal.get_loc(seg.index[0]), P.cal.get_loc(seg.index[-1])), P)),
                                "QQQ": fmt_m(metrics(bench_nav(P, "QQQ", P.cal.get_loc(seg.index[0]), P.cal.get_loc(seg.index[-1])), P))}
spy200 = P.adj["SPY"] > P.adj["SPY"].rolling(200).mean()
ev_all["bull"] = [bool(spy200.get(d, True)) if pd.notna(d) else None for d in ev_all.entry]
stress["regime"] = {"SPY above 200d": summarize(ev_all[ev_all.bull.eq(True)], "x252_SPY"),
                    "SPY below 200d": summarize(ev_all[ev_all.bull.eq(False)], "x252_SPY")}
# Survivorship: buys with no usable prices, at approximate public-record outcomes
# (NOT from the price file): Hertz 2014-15 calls expired worthless (stock ~-35%
# over a year), SunEdison ~-60% within a year and bankrupt 2016, Slack ~+20% vs SPY.
approx = {"HTZ": -0.35 - 0.02, "SUNE": -0.60 - 0.02, "WORK": 0.20}
ok = ev_all[ev_all.status == "ok"]["x252_SPY"].dropna().tolist()
miss = [approx[t] for t in buys.ticker if t in approx]
stress["survivorship (approx)"] = dict(without=pct(np.mean(ok)), with_missing=pct(np.mean(ok + miss)),
                                       n_missing=len(miss))
R["stress"] = stress

# ------------------------------------------------------------ 6. why features
earn = pd.read_csv(Path(__file__).parent / "data" / "pelosi_earnings.csv")
earn["d"] = pd.to_datetime(earn["Earnings Date"].str[:10])
earn["ticker"] = earn.ticker.replace({"FB": "META", "SQ": "XYZ"})
why = []
for _, e in buys[buys.mid_usd >= 250_000].iterrows():
    t = e.px_ticker
    i = P.idx_on_or_after(e.tdate)
    if i is None or not P.has(t, i) or i < 260:
        why.append(dict(tdate=str(e.tdate.date()), ticker=e.ticker, kind=e.kind, note="no price history"))
        continue
    a, s = P.adj[t], P.adj["SPY"]
    def rel(n): return float(a.iloc[i] / a.iloc[i - n] - 1 - (s.iloc[i] / s.iloc[i - n] - 1))
    hi52 = float(a.iloc[i] / a.iloc[i - 252:i + 1].max() - 1)
    ed = earn[earn.ticker == t].d.sort_values()
    nxt = ed[ed > e.tdate]
    prv = ed[ed <= e.tdate]
    S_raw = P.close[t].iloc[i] * P.split_after[t].iloc[i]
    j = entry_index(P, e, REAL)
    fwd = None
    if j is not None and j + 252 < len(P.cal) and P.has(t, j + 252):
        fwd = float(a.iloc[j + 252] / a.iloc[j] - 1 - (s.iloc[j + 252] / s.iloc[j] - 1))
    why.append(dict(tdate=str(e.tdate.date()), ticker=e.ticker, kind=e.kind, usd=e.mid_usd,
                    mom1m=rel(21), mom3m=rel(63), mom12m=rel(252), off_52w_high=hi52,
                    smh3m=float(P.adj["SMH"].iloc[i] / P.adj["SMH"].iloc[i - 63] - 1 - (s.iloc[i] / s.iloc[i - 63] - 1)),
                    days_to_earn=int((nxt.iloc[0] - e.tdate).days) if len(nxt) else None,
                    days_since_earn=int((e.tdate - prv.iloc[-1]).days) if len(prv) else None,
                    spy_above_200d=bool(spy200.iloc[i]), vix=float(P.close["^VIX"].iloc[i]),
                    moneyness=(float(S_raw / e.strike) if pd.notna(e.strike) else None),
                    fwd252_vs_spy_public=fwd))
why = pd.DataFrame(why)
why.to_csv(OUT / "why_features.csv", index=False)
pred = {}
w = why.dropna(subset=["fwd252_vs_spy_public"])
for c in ("mom1m", "mom3m", "mom12m", "off_52w_high", "smh3m", "days_to_earn", "days_since_earn", "vix"):
    x = w[[c, "fwd252_vs_spy_public"]].dropna()
    if len(x) >= 8:
        rho, pv = spearmanr(x[c], x["fwd252_vs_spy_public"])
        pred[c] = dict(n=len(x), rho=round(float(rho), 2), p=round(float(pv), 3))
R["why_predictive"] = pred

# ------------------------------------------------------------ 7. congress-wide (2023-2026, old Alpaca file)
def congress_wide(path):
    px = json.load(open(path))
    cal = pd.DatetimeIndex(pd.to_datetime(px["AMZN"]["d"]))
    close = {}
    for t, v in px.items():
        s = pd.Series(v["c"], index=pd.to_datetime(v["d"]))
        close[t] = s.reindex(cal)
    C = pd.DataFrame(close)
    spy = P.adj["SPY"].reindex(cal).ffill()
    h = pd.read_parquet(Path(__file__).parent / "data" / "congress_hf.parquet")
    h = h[h.supersededAt.isna()].copy()
    h["pub"] = (h.firstAvailableAt - pd.Timedelta(hours=5)).dt.normalize()
    h = h[(h.pub >= "2023-01-01") & (h.pub <= cal[-1] - pd.Timedelta(days=200))]
    h["is_call"] = h.comment.fillna("").str.contains("call option", case=False) & (h.assetTypeCode == "OP")
    h["stock"] = h.assetTypeCode.isna() | (h.assetTypeCode == "ST")
    h = h[(h.stock | h.is_call) & h.action.isin(["purchase", "sale"]) & h.ticker.notna()]
    h["ticker"] = h.ticker.str.upper().replace({"FB": "META", "SQ": "XYZ", "BRK.B": "BRK.B"})
    pos = cal.searchsorted(h.pub.values, side="right")
    h["i"] = pos
    rows = []
    for (t, i), g in zip(zip(h.ticker, h.i), h.itertuples()):
        if t not in C or i >= len(cal):
            continue
        c0 = C[t].iloc[i]
        if not np.isfinite(c0):
            continue
        r = {"member": g.displayName or g.filerLast, "chamber": g.chamber, "ticker": t, "action": g.action,
             "is_call": g.is_call, "usd": (g.amountLow or 0), "pub": g.pub, "entry": cal[i]}
        for hh in (21, 63, 126):
            j = i + hh
            if j < len(cal) and np.isfinite(C[t].iloc[j]):
                r[f"x{hh}"] = C[t].iloc[j] / c0 - 1 - 0.001 - (spy.iloc[j] / spy.iloc[i] - 1)
        rows.append(r)
    E = pd.DataFrame(rows)
    E.to_csv(OUT / "congress_events.csv", index=False)
    res = {"events": len(E), "tickers": int(E.ticker.nunique()), "members": int(E.member.nunique()),
           "coverage": f"{cal[0].date()} to {cal[-1].date()}"}
    B = E[(E.action == "purchase") & ~E.is_call]
    res["all stock buys"] = {h_: summarize(B, f"x{h_}", "pub") for h_ in (21, 63, 126)}
    res["all stock sells"] = {h_: summarize(E[(E.action == "sale") & ~E.is_call], f"x{h_}", "pub") for h_ in (21, 63, 126)}
    res["call-option buys (the scanner's gap)"] = {h_: summarize(E[(E.action == "purchase") & E.is_call], f"x{h_}", "pub") for h_ in (21, 63, 126)}
    res["big stock buys ($250k+)"] = {h_: summarize(B[B.usd >= 250_001], f"x{h_}", "pub") for h_ in (21, 63, 126)}
    res["house"] = summarize(B[B.chamber == "house"], "x63", "pub")
    res["senate"] = summarize(B[B.chamber == "senate"], "x63", "pub")
    # member skill, walk-forward by quarter: rank members on buys whose 63-day
    # window had closed before the quarter began; follow the top 10 next quarter.
    Bm = E[E.action == "purchase"].copy()
    Bm["done"] = Bm.entry + pd.Timedelta(days=95)
    picks, base_q = [], []
    for q in pd.period_range("2024Q1", (cal[-1] - pd.Timedelta(days=95)).to_period("Q"), freq="Q"):
        qs, qe = q.start_time, q.end_time
        hist = Bm[(Bm.done < qs) & Bm.x63.notna()]
        sc = hist.groupby("member").x63.agg(["mean", "count"])
        sc = sc[sc["count"] >= 5].sort_values("mean", ascending=False)
        top = set(sc.head(10).index)
        cur = Bm[(Bm.entry >= qs) & (Bm.entry <= qe)]
        picks.append(cur[cur.member.isin(top)])
        base_q.append(cur)
    PK, BQ = pd.concat(picks), pd.concat(base_q)
    res["member skill walk-forward (top 10 by past record)"] = summarize(PK, "x63", "pub")
    res["same quarters, all members"] = summarize(BQ, "x63", "pub")
    bot = []
    for q in pd.period_range("2024Q1", (cal[-1] - pd.Timedelta(days=95)).to_period("Q"), freq="Q"):
        hist = Bm[(Bm.done < q.start_time) & Bm.x63.notna()]
        sc = hist.groupby("member").x63.agg(["mean", "count"])
        sc = sc[sc["count"] >= 5].sort_values("mean")
        cur = Bm[(Bm.entry >= q.start_time) & (Bm.entry <= q.end_time)]
        bot.append(cur[cur.member.isin(set(sc.head(10).index))])
    res["bottom 10 by past record (control)"] = summarize(pd.concat(bot), "x63", "pub")
    pel = E[E.member.str.contains("Pelosi", na=False) & (E.action == "purchase")]
    res["pelosi in this window"] = summarize(pel, "x63", "pub")
    ms = Bm.groupby("member").x63.agg(["mean", "count"])
    ms = ms[ms["count"] >= 10].sort_values("mean", ascending=False)
    res["members with 10+ buys"] = int(len(ms))
    res["pelosi rank"] = (int(list(ms.index).index([m for m in ms.index if "Pelosi" in m][0]) + 1)
                          if any("Pelosi" in m for m in ms.index) else None)
    # momentum before trade, does it predict? (why-feature test at scale)
    mom = []
    for g in B.itertuples():
        i = cal.get_loc(g.entry)
        if i >= 63 and np.isfinite(C[g.ticker].iloc[i - 63]) and pd.notna(g.x63):
            mom.append((C[g.ticker].iloc[i] / C[g.ticker].iloc[i - 63] - 1 - (spy.iloc[i] / spy.iloc[i - 63] - 1), g.x63))
    m = np.array(mom)
    rho, pv = spearmanr(m[:, 0], m[:, 1])
    res["pre-trade 3m momentum -> next 63d"] = dict(n=len(m), rho=round(float(rho), 3), p=round(float(pv), 4))
    return res


if len(sys.argv) > 1:
    R["congress_wide"] = congress_wide(sys.argv[1])

json.dump(R, open(OUT / "results.json", "w"), indent=1, default=str)
print(json.dumps({k: R[k] for k in ("ledger", "walk_forward_pick", "positions", "vs_nanc")}, indent=1, default=str))
