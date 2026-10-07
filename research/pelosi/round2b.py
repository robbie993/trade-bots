"""Round 2b: walk-forward pick among copy holding rules, sector and trade-type
breakdown, full metric table, and where the modeled options copy loses."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from engine import Prices, bench_nav, build_positions, metrics, simulate
from ledger import build

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "out"
REAL = "Public, next day (realistic)"
P = Prices()
led = build()
R = {}


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 1)


RULES = {"sell when she sells": dict(), "never sell": dict(exit_on_sale=False),
         "hold 6 months": dict(exit_on_sale=False, hold_td=126),
         "hold 1 year": dict(exit_on_sale=False, hold_td=252),
         "hold 2 years": dict(exit_on_sale=False, hold_td=504),
         "hold 3 years": dict(exit_on_sale=False, hold_td=756),
         "never sell, equal weight": dict(exit_on_sale=False, weight="equal"),
         "sell when she sells, equal weight": dict(weight="equal")}
SPL = {"train 2014-2020": ("2014-12-01", "2020-12-31"), "validate 2021-2022": ("2021-01-01", "2022-12-31"),
       "test 2023-2026": ("2023-01-01", "2026-12-31")}


def seg_cagr(nav, a, b):
    s = nav[(nav.index >= a) & (nav.index <= b)]
    s = s / s.iloc[0]
    y = (s.index[-1] - s.index[0]).days / 365.25
    return s.iloc[-1] ** (1 / y) - 1, (s / s.cummax() - 1).min()


navs = {}
for k, kw in RULES.items():
    pos, _ = build_positions(P, led, REAL, **kw)
    navs[k] = simulate(P, pos, min(p.i0 for p in pos))[0]
i0 = P.cal.get_loc(navs["sell when she sells"].index[0])
spy, qqq = bench_nav(P, "SPY", i0), bench_nav(P, "QQQ", i0)
# Rules are scored by an outsider's path: each split's positions are only the
# ones opened inside the split, so nothing from later filings leaks backwards.
wf = {}
for k, kw in RULES.items():
    row = {}
    for s, (a, b) in SPL.items():
        ia, ib = P.idx_on_or_after(a), min(P.idx_on_or_after(b) or len(P.cal) - 1, len(P.cal) - 1)
        pos, _ = build_positions(P, led, REAL, end_i=ib, **kw)
        pos = [p for p in pos if p.i0 >= ia]
        if not pos:
            continue
        nav = simulate(P, pos, ia, ib)[0]
        c, dd = seg_cagr(nav, a, b)
        cs, _ = seg_cagr(spy, a, b)
        cq, _ = seg_cagr(qqq, a, b)
        row[s] = dict(cagr=pct(c), vs_spy=pct(c - cs), vs_qqq=pct(c - cq), maxdd=pct(dd))
    wf[k] = row
R["walk_forward_rules"] = wf
best = max(wf, key=lambda k: wf[k]["train 2014-2020"]["vs_spy"])
R["train_pick"] = dict(rule=best, **wf[best])

full = {}
for k, nav in navs.items():
    m = metrics(nav, P)
    full[k] = {x: (pct(m[x]) if x in ("total", "cagr", "vol", "maxdd", "alpha") else round(m[x], 2))
               for x in ("total", "cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "beta", "alpha")}
R["full_metrics"] = full

# Sector / trade type: 1-year excess vs SPY per buy (realistic entry)
ev = pd.read_csv(OUT / "events_realistic.csv")
SECT = {"AAPL": "Big tech", "MSFT": "Big tech", "AMZN": "Big tech", "GOOGL": "Big tech", "GOOG": "Big tech",
        "META": "Big tech", "NVDA": "Semis", "AVGO": "Semis", "MU": "Semis", "INTC": "Semis",
        "CRM": "Software/cyber", "PANW": "Software/cyber", "CRWD": "Software/cyber", "DBX": "Software/cyber",
        "WORK": "Software/cyber", "TEM": "Software/cyber", "RBLX": "Software/cyber",
        "NFLX": "Media/consumer", "DIS": "Media/consumer", "TSLA": "Media/consumer", "UBER": "Media/consumer",
        "PYPL": "Payments", "XYZ": "Payments", "V": "Payments", "AXP": "Payments",
        "AB": "Financials/other", "T": "Financials/other", "HTZ": "Financials/other", "SUNE": "Energy/industrial",
        "VST": "Energy/industrial", "BE": "Energy/industrial"}
tk = "px_ticker" if "px_ticker" in ev else "ticker"
ev["sector"] = ev[tk].map(SECT).fillna("Other")
col = "x252_SPY"
R["by_sector_1y_vs_spy"] = {s: dict(n=int(g[col].notna().sum()), mean=pct(g[col].mean()), hit=pct((g[col] > 0).mean()))
                            for s, g in ev.groupby("sector")}
R["by_kind_1y_vs_spy"] = {s: dict(n=int(g[col].notna().sum()), mean=pct(g[col].mean()), hit=pct((g[col] > 0).mean()))
                          for s, g in ev.groupby("kind")}

# Modeled options copy: which positions sank it
pos, _ = build_positions(P, led, REAL, use_options=True)
rows = [dict(ticker=p.ticker, entry=str(P.cal[p.i0].date()), exit=str(P.cal[p.i1].date()), opt=p.is_opt,
             ret=pct(p.path[-1] - 1), worst=pct(p.path.min() - 1), usd=p.usd) for p in pos]
OP = pd.DataFrame(rows)
R["options_positions_worst"] = OP[OP.opt].sort_values("ret").head(10).to_dict("records")
R["options_positions_count"] = dict(n=int(OP.opt.sum()), lost_over_half=int(((OP.ret < -50) & OP.opt).sum()),
                                    wiped=int(((OP.ret <= -95) & OP.opt).sum()))
json.dump(R, open(OUT / "round2b.json", "w"), indent=1, default=str)
print(json.dumps(R, indent=1, default=str))
