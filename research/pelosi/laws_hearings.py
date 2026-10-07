"""Did Pelosi's buys line up with congressional hearings or laws, and did those
buys do better? Public-record test (laws 113th-119th, hearings with company
witnesses, both from channels/).

For each of her stock and call buys (trade date):
  hearing   the company testified at a hearing within 90 days before or after
  tech law  a law flagged tech was signed within 90 days after (tech names only)
Placebo: the same stock on 200 random days 1-24 months away; if her dates are
special, events should sit closer to her dates than to random ones.
Return: 1-year excess vs SPY from the copy entry (next session after filing),
event buys vs her other buys. Writes out/laws_hearings.json."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import Prices, entry_index
from ledger import build

warnings.filterwarnings("ignore")
C = Path(__file__).parent / "channels"
OUT = Path(__file__).parent / "out"
REAL = "Public, next day (realistic)"
ALIAS = {"META": {"META", "FB"}, "GOOGL": {"GOOGL", "GOOG"}, "GOOG": {"GOOGL", "GOOG"}}
TECH = set("AAPL MSFT NVDA GOOGL GOOG META FB AMZN AVGO CRM CRWD PANW ADBE AMD INTC QCOM CSCO TSLA NFLX "
           "PYPL V MA DIS ROKU PLTR TEM VST ARM SNOW".split())
W = 90
P = Prices()
led = build()
B = led[led.kind.isin(["buy_stock", "buy_call"])].copy()

H = pd.read_csv(C / "hearings.csv.gz")
H["d"] = pd.to_datetime(H.first_held, errors="coerce")
hear = {}
for d, cs in zip(H.d, H.companies_in_witnesses):
    if pd.notna(d) and isinstance(cs, str):
        for t in cs.split("; "):
            hear.setdefault(t.strip(), []).append(d)
hear = {k: np.array(sorted(v), dtype="datetime64[ns]") for k, v in hear.items()}
L = pd.read_csv(C / "laws.csv")
tech_laws = np.array(sorted(pd.to_datetime(L[L.sector_tech].became_law, errors="coerce").dropna()), dtype="datetime64[ns]")


def hearing_near(t, d):
    ds = np.concatenate([hear.get(x, np.array([], dtype="datetime64[ns]")) for x in ALIAS.get(t, {t})])
    if not len(ds):
        return False
    return bool((np.abs((ds - np.datetime64(d)) / np.timedelta64(1, "D")) <= W).any())


def law_after(d):
    x = (tech_laws - np.datetime64(d)) / np.timedelta64(1, "D")
    return int(((x >= 0) & (x <= W)).sum())


rng = np.random.default_rng(1)
rows = []
for _, e in B.iterrows():
    t, d = e.px_ticker, pd.Timestamp(e.tdate)
    if not isinstance(t, str):
        continue
    rnd = [d + pd.Timedelta(days=int(o)) for o in rng.choice(np.r_[-730:-30, 30:730], 200)]
    rnd = [r for r in rnd if pd.Timestamp("2013-01-01") <= r <= pd.Timestamp("2026-06-01")]
    i = entry_index(P, e, REAL)
    x = np.nan
    if i is not None and i + 252 < len(P.cal) and P.has(t, i) and np.isfinite(P.adj[t].iloc[i + 252]):
        x = P.adj[t].iloc[i + 252] / P.adj[t].iloc[i] - P.adj["SPY"].iloc[i + 252] / P.adj["SPY"].iloc[i]
    rows.append(dict(ticker=t, date=d, kind=e.kind, x252=x,
                     hearing=hearing_near(t, d), hearing_rnd=float(np.mean([hearing_near(t, r) for r in rnd])),
                     tech=t in TECH, laws=law_after(d), laws_rnd=float(np.mean([law_after(r) for r in rnd]))))
D = pd.DataFrame(rows)


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 1)


T = D[D.tech]
res = dict(
    buys=len(D), with_1y_return=int(D.x252.notna().sum()),
    hearings=dict(
        share_of_buys_within_90d_of_company_hearing=pct(D.hearing.mean()),
        same_stock_random_days=pct(D.hearing_rnd.mean()),
        names_with_hearings=sorted(D[D.hearing].ticker.unique().tolist()),
        x252_hearing_buys=pct(D[D.hearing].x252.mean()), n_hearing=int(D[D.hearing].x252.notna().sum()),
        x252_other_buys=pct(D[~D.hearing].x252.mean()), n_other=int(D[~D.hearing].x252.notna().sum())),
    tech_laws=dict(
        tech_buys=len(T), avg_tech_laws_signed_within_90d_after=round(T.laws.mean(), 2),
        same_stock_random_days=round(T.laws_rnd.mean(), 2),
        x252_buys_with_law_after=pct(T[T.laws > 0].x252.mean()), n_with=int(T[T.laws > 0].x252.notna().sum()),
        x252_buys_without=pct(T[T.laws == 0].x252.mean()), n_without=int(T[T.laws == 0].x252.notna().sum())),
)
# paired check: is her date closer to a hearing than random dates for the same stock?
res["hearings"]["buys_where_hearing_more_likely_than_random"] = f"{int((D.hearing > D.hearing_rnd).sum())} of {len(D)}"
res["tech_laws"]["buys_with_more_laws_than_random"] = f"{int((T.laws > T.laws_rnd).sum())} of {len(T)}"
D.to_csv(OUT / "laws_hearings_events.csv", index=False)
json.dump(res, open(OUT / "laws_hearings.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
