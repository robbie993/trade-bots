"""Congress-wide test over the full history (2014-2026) using the yearly
congress_prices_YYYY.parquet files from the PC. Same rules as study.py's
congress_wide (enter at the close of the first session after the trade became
public, 10 bps cost, excess vs SPY), split by period, with member-skill
walk-forward from 2015. Writes out/congress_full.json and out/congress_full_events.csv."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from engine import cluster_boot

warnings.filterwarnings("ignore")
D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"
PERIODS = {"2014-2018": ("2014-01-01", "2018-12-31"), "2019-2022": ("2019-01-01", "2022-12-31"),
           "2023-2026": ("2023-01-01", "2026-12-31"), "all": ("2014-01-01", "2026-12-31")}
H = (21, 63, 126, 252)


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def summarize(ev, col):
    x = ev[[col, "pub"]].dropna()
    if len(x) < 5:
        return dict(n=int(len(x)))
    m, lo, hi = cluster_boot(x[col], x["pub"])
    w = x[col].clip(x[col].quantile(0.01), x[col].quantile(0.99)).mean()
    return dict(n=int(len(x)), mean=pct(m), lo=pct(lo), hi=pct(hi), median=pct(x[col].median()),
                trimmed=pct(w), hit=pct((x[col] > 0).mean()))


def load_prices():
    px = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "adj_close"])
                    for f in sorted(D.glob("congress_prices_*.parquet"))])
    C = px.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
    return C


def main():
    C = load_prices()
    # Data-quality screen: foreign OTC lines (5 letters ending in F) and any
    # ticker with a one-day move over 3x or under 1/3 (bad prints or splits the
    # adjusted close missed) are dropped and counted.
    lr = np.log(C / C.ffill().shift()).abs()
    bad = set(lr.columns[(lr > np.log(3)).any()]) | {t for t in C.columns if re.fullmatch(r"[A-Z]{4}F", t)}
    bad.discard("SPY")
    C = C.drop(columns=sorted(bad))
    cal = C.index
    spy = C["SPY"].ffill()
    failed = set(open(D / "congress_prices_failed.txt").read().split())
    h = pd.read_parquet(D / "congress_hf.parquet")
    h = h[h.supersededAt.isna()].copy()
    h["pub"] = (h.firstAvailableAt - pd.Timedelta(hours=5)).dt.normalize()
    h = h[(h.pub >= "2014-01-01")]
    h["is_call"] = h.comment.fillna("").str.contains("call option", case=False) & (h.assetTypeCode == "OP")
    h["stock"] = h.assetTypeCode.isna() | (h.assetTypeCode == "ST")
    h = h[(h.stock | h.is_call) & h.action.isin(["purchase", "sale"]) & h.ticker.notna()]
    h["ticker"] = h.ticker.str.upper().str.strip().replace({"FB": "META", "SQ": "XYZ"})
    h["i"] = cal.searchsorted(h.pub.values, side="right")
    cand = len(h)
    rows, miss = [], 0
    Cv = C.to_numpy()
    col = {t: k for k, t in enumerate(C.columns)}
    sv = spy.to_numpy()
    for g in h.itertuples():
        k, i = col.get(g.ticker), g.i
        if k is None or i >= len(cal) or not np.isfinite(Cv[i, k]):
            miss += 1
            continue
        c0 = Cv[i, k]
        r = {"member": g.displayName or g.filerLast, "chamber": g.chamber, "ticker": g.ticker,
             "action": g.action, "is_call": g.is_call, "usd": (g.amountLow or 0), "pub": g.pub,
             "entry": cal[i], "lag": (g.pub - pd.Timestamp(g.transactionDate)).days
             if pd.notna(g.transactionDate) else np.nan}
        for hh in H:
            j = i + hh
            if j < len(cal) and np.isfinite(Cv[j, k]):
                r[f"x{hh}"] = Cv[j, k] / c0 - 1 - 0.001 - (sv[j] / sv[i] - 1)
        if i >= 63 and np.isfinite(Cv[i - 63, k]):
            r["mom63"] = Cv[i, k] / Cv[i - 63, k] - 1 - (sv[i] / sv[i - 63] - 1)
        rows.append(r)
    E = pd.DataFrame(rows)
    E.to_csv(OUT / "congress_full_events.csv.gz", index=False)
    res = {"candidate trades since 2014": cand, "priced": len(E), "dropped (no price)": miss,
           "dropped share": pct(miss / cand), "members": int(E.member.nunique()),
           "tickers": int(E.ticker.nunique()), "price coverage": f"{cal[0].date()} to {cal[-1].date()}",
           "tickers with no Yahoo history": len(failed),
           "tickers dropped by data-quality screen": len(bad)}
    B = E[(E.action == "purchase") & ~E.is_call]
    S = E[(E.action == "sale") & ~E.is_call]
    Cb = E[(E.action == "purchase") & E.is_call]
    for name, (a, b) in PERIODS.items():
        def sel(X, a=a, b=b):
            return X[(X.pub >= a) & (X.pub <= b)]

        res[name] = {
            "stock buys": {hh: summarize(sel(B), f"x{hh}") for hh in H},
            "stock sells": {hh: summarize(sel(S), f"x{hh}") for hh in (63, 126, 252)},
            "call-option buys": {hh: summarize(sel(Cb), f"x{hh}") for hh in (63, 126, 252)},
            "big stock buys ($250k+)": {hh: summarize(sel(B[B.usd >= 250_001]), f"x{hh}") for hh in (63, 126, 252)},
            "house buys 126d": summarize(sel(B[B.chamber == "house"]), "x126"),
            "senate buys 126d": summarize(sel(B[B.chamber == "senate"]), "x126"),
            "fast filers (<=15d) buys 126d": summarize(sel(B[B.lag <= 15]), "x126"),
        }
    # Member skill, walk-forward by quarter: rank members on buys whose 126-day
    # window closed before the quarter began; follow the top 10 next quarter.
    Bm = E[E.action == "purchase"].copy()
    Bm["done"] = Bm.entry + pd.Timedelta(days=190)
    top_l, bot_l, all_l = [], [], []
    for q in pd.period_range("2015Q1", (cal[-1] - pd.Timedelta(days=190)).to_period("Q"), freq="Q"):
        hist = Bm[(Bm.done < q.start_time) & Bm.x126.notna()]
        sc = hist.groupby("member").x126.agg(["mean", "count"])
        sc = sc[sc["count"] >= 5].sort_values("mean", ascending=False)
        cur = Bm[(Bm.entry >= q.start_time) & (Bm.entry <= q.end_time)].assign(q=str(q))
        top_l.append(cur[cur.member.isin(set(sc.head(10).index))])
        bot_l.append(cur[cur.member.isin(set(sc.tail(10).index))])
        all_l.append(cur)
    TP, BT, AL = pd.concat(top_l), pd.concat(bot_l), pd.concat(all_l)
    res["member skill walk-forward 126d"] = {
        "top 10 by past record": summarize(TP, "x126"),
        "bottom 10 by past record": summarize(BT, "x126"),
        "all members same quarters": summarize(AL, "x126"),
        "top 10 by period": {n: summarize(TP[(TP.pub >= a) & (TP.pub <= b)], "x126")
                             for n, (a, b) in PERIODS.items() if n != "all"},
    }
    # Does a member's first-half record predict their second half? (persistence)
    ms = Bm.dropna(subset=["x126"])
    first = ms[ms.pub < "2020-01-01"].groupby("member").x126.agg(["mean", "count"])
    second = ms[ms.pub >= "2020-01-01"].groupby("member").x126.agg(["mean", "count"])
    j = first[first["count"] >= 10].join(second[second["count"] >= 10], lsuffix="_a", rsuffix="_b", how="inner")
    if len(j) > 5:
        rho, pv = spearmanr(j.mean_a, j.mean_b)
        res["member persistence 2014-19 -> 2020-26"] = dict(members=len(j), rho=round(float(rho), 3), p=round(float(pv), 3))
    m = B.dropna(subset=["mom63", "x126"])
    rho, pv = spearmanr(m.mom63, m.x126)
    res["pre-trade 3m momentum -> next 126d"] = dict(n=len(m), rho=round(float(rho), 3), p=round(float(pv), 4))
    pel = B[B.member.str.contains("Pelosi", na=False)]
    res["pelosi stock buys 126d"] = summarize(pel, "x126")
    ranks = Bm.dropna(subset=["x126"]).groupby("member").x126.agg(["mean", "count"])
    ranks = ranks[ranks["count"] >= 20].sort_values("mean", ascending=False)
    res["members with 20+ buys"] = len(ranks)
    res["top 10 members all-time (hindsight, 126d mean %)"] = {
        k: [pct(v.mean_), int(v.count_)] for k, v in
        ranks.head(10).rename(columns={"mean": "mean_", "count": "count_"}).iterrows()}
    json.dump(res, open(OUT / "congress_full.json", "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
