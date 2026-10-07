"""Committee power and jurisdiction, all members 2014-2026.

For each stock buy: at the trade date, was the member a committee chair or
ranking member, a party leader, on a power committee, and did the stock's
industry fall under one of their committees? 6-month return vs SPY at the
public date (copyable) and at the trade date (the member's own timing).
Writes out/committees_test.json."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import cluster_boot

warnings.filterwarnings("ignore")
D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def box(x, cl):
    x = pd.Series(x).astype(float).dropna()
    if len(x) < 5:
        return dict(n=int(len(x)))
    m, lo, hi = cluster_boot(x, pd.Series(cl).loc[x.index])
    return dict(n=int(len(x)), mean=pct(m), lo=pct(lo), hi=pct(hi), hit=pct((x > 0).mean()))


SECTOR = {
    "defense": "LMT RTX NOC GD BA LHX HII TXT LDOS SAIC BAH CACI KTOS AXON",
    "banks": "JPM BAC WFC C GS MS USB PNC SCHW TFC COF AXP BK STT",
    "health": "PFE MRK JNJ ABBV LLY UNH CVS BMY AMGN GILD CI HUM ELV MDT ABT TMO DHR REGN VRTX BIIB MRNA",
    "energy": "XOM CVX COP OXY SLB EOG KMI PSX MPC VLO HAL DVN WMB OKE ET EPD NEE DUK SO",
    "tech": "AAPL MSFT GOOGL GOOG META AMZN NVDA NFLX CRM ORCL INTC AMD QCOM AVGO CSCO IBM",
    "telecom": "T VZ TMUS CMCSA CHTR DIS",
    "ag/food": "ADM DE TSN BG CTVA MOS CF NTR",
    "transport": "UPS FDX UNP CSX NSC DAL UAL AAL LUV",
}
T2S = {t: s for s, ts in SECTOR.items() for t in ts.split()}
JUR = {   # committee name pattern -> sectors it oversees
    r"Armed Services|Defense|Intelligence": {"defense"},
    r"Financial Services|Banking": {"banks"},
    r"Energy and Commerce": {"health", "energy", "tech", "telecom"},
    r"Health, Education|Finance\b|Ways and Means|Veterans": {"health"},
    r"Natural Resources|Energy and Natural|Environment and Public Works": {"energy"},
    r"Judiciary": {"tech"},
    r"Commerce, Science|Senate Committee on Commerce|Science, Space": {"tech", "telecom", "transport"},
    r"Agriculture": {"ag/food"},
    r"Transportation": {"transport"},
}
POWER = r"Ways and Means|Energy and Commerce|Financial Services|Appropriations|Armed Services|Intelligence|Rules|Senate Committee on Finance|Banking|Senate Committee on Commerce"

c = pd.read_csv(Path(__file__).parent / "channels" / "committees.csv", parse_dates=["start", "end"])
c["end"] = c.end.fillna(pd.Timestamp("2027-01-03"))
c["start"] = c.start.fillna(pd.Timestamp("2013-01-03"))
full = c[c.subcommittee.isna()]

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
h = h[(h.pub >= "2014-01-01") & h.td.notna()]
h["ticker"] = h.ticker.str.upper().str.strip().replace({"FB": "META", "SQ": "XYZ"})

by_member = {b: g for b, g in full.groupby("bioguide")}
rows = []
for g in h.itertuples():
    k = col.get(g.ticker)
    if k is None:
        continue
    seats = by_member.get(g.memberId)
    if seats is not None:
        seats = seats[(seats.start <= g.td) & (seats.end > g.td)]
    names = list(seats.committee.astype(str)) if seats is not None else []
    roles = set(seats.role) if seats is not None else set()
    sec = T2S.get(g.ticker)
    juris = bool(sec) and any(re.search(p, n) and sec in s for n in names for p, s in JUR.items())
    r = dict(ticker=g.ticker, member=g.displayName, pub=g.pub, sector=sec or "other", has_seats=seats is not None and len(names) > 0,
             chair=bool(roles & {"chair"}), ranking=bool(roles & {"ranking"}),
             leader=bool(roles & {"speaker", "majority leader", "minority leader", "majority whip", "minority whip"}),
             power=any(re.search(POWER, n) for n in names), juris=juris)
    for tag, d in (("pub", g.pub), ("trade", g.td)):
        i = cal.searchsorted(d, side="right" if tag == "pub" else "left")
        if i + 126 < len(cal) and np.isfinite(Cv[i, k]) and np.isfinite(Cv[i + 126, k]):
            r["x_" + tag] = Cv[i + 126, k] / Cv[i, k] - 1 - 0.001 - (sv[i + 126] / sv[i] - 1)
    rows.append(r)
Z = pd.DataFrame(rows)
Z = Z[Z.has_seats]
Z.to_csv(OUT / "committees_buys.csv.gz", index=False)
res = {"buys with committee data": len(Z), "members": int(Z.member.nunique())}
for name, m in {"committee chair": Z.chair, "ranking member": Z.ranking, "party leader": Z.leader,
                "on a power committee": Z.power, "stock in their committee's industry": Z.juris}.items():
    res[name] = {"yes": {t: box(Z.loc[m, "x_" + t], Z.loc[m, "pub"]) for t in ("pub", "trade")},
                 "no": {t: box(Z.loc[~m, "x_" + t], Z.loc[~m, "pub"]) for t in ("pub", "trade")}}
S = Z[Z.sector != "other"]
res["by sector, own committee vs not (trade timing)"] = {
    s: {"own committee": box(g.loc[g.juris, "x_trade"], g.loc[g.juris, "pub"]),
        "other members": box(g.loc[~g.juris, "x_trade"], g.loc[~g.juris, "pub"])} for s, g in S.groupby("sector")}
# Same stock, same quarter: own-committee buyers minus other members' buyers
Z["q"] = pd.to_datetime(Z.pub).dt.to_period("Q").astype(str)
cell = Z.dropna(subset=["x_trade"]).groupby(["ticker", "q", "juris"]).x_trade.mean().unstack()
cell = cell.dropna()
d = cell[True] - cell[False]
res["same stock and quarter: own committee minus others"] = dict(
    cells=len(d), mean=pct(d.mean()), median=pct(d.median()),
    t=round(float(d.mean() / (d.std() / np.sqrt(len(d)))), 2) if len(d) > 2 else None,
    tech_only=pct(d[[T2S.get(t) == "tech" for t, _ in d.index]].mean()))
json.dump(res, open(OUT / "committees_test.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
