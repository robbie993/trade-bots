"""Daily total-return prices for the Atlas Core ETF universe.

Two sources, spliced on returns, and every row keeps the name of the source
it came from. Nothing here is fetched at import; `build` writes a panel once
and `load` reads it.

* **2000-01 to 2020-11-10: Yahoo adjusted closes, via Microsoft Qlib's public
  US dataset** (`qlib_data_us_1d_latest.zip`, GitHub release
  SunsetWolf/qlib_dataset v2). Adjusted for splits *and* dividends, so a
  return off it is a total return.
* **2020-11-11 to 2026-07-30: Alpaca SIP daily bars with `adjustment=all`**,
  pulled on 2026-08-01 by trade-bot-2's `insider_research/fetch_prices.py`
  and kept in `insider_research/etf_prices.json` inside trade.zip.

Why the splice sits at 2020-11-10 and not at 2016, where both overlap:
Alpaca's daily "close" can be a late extended-hours print. On 2020-03-12
SPY's official close-to-close move was -9.57% (Yahoo agrees); Alpaca's bar
says -6.93%. Over 2016-2020 the two agree on cumulative return to within
1%, so the noise washes out over months, but each source is used only where
the other is missing. From 2020-11 Alpaca is the only source in reach; its
SPY returns agree with Yahoo's raw SPY closes to 0.9 bp a day on average.

What this universe is NOT: survivorship-free for stocks. Every name in the
Qlib dump was still listed in Nov 2020, so a stock-selection backtest on it
would quietly drop every company that went bust. ETFs do not have that
problem in the same way (all 14 here still trade), which is why Atlas Core
starts on ETFs. See `fundamentals.py`.

The cloud session that built this could not reach Yahoo, Stooq, Alpaca or
FRED directly; GitHub release assets were the one route in. `pc_fetch.py`
rebuilds the same panel from a single source (yfinance) on a machine that
can reach Yahoo, for a cross-check.
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
        "GLD", "SLV", "DBC", "VNQ", "UUP"]

#: What each ETF is, so a report can say "the strategy sat in long Treasuries"
#: rather than a ticker.
ASSET_CLASS = {
    "SPY": "US large cap", "QQQ": "US tech/growth", "IWM": "US small cap",
    "EFA": "developed ex-US", "EEM": "emerging markets",
    "TLT": "long Treasuries", "IEF": "7-10y Treasuries",
    "LQD": "IG credit", "HYG": "high yield", "GLD": "gold", "SLV": "silver",
    "DBC": "commodities", "VNQ": "US REITs", "UUP": "US dollar",
}

QLIB_END = pd.Timestamp("2020-11-10")

DEFAULT_DIR = Path(os.environ.get("ATLAS_DATA_DIR", "/mnt/project-files/atlas_research"))
PANEL = "etf_daily.csv"
SOURCES = "etf_sources.json"


def _qlib_series(zf: zipfile.ZipFile, cal: pd.DatetimeIndex, sym: str) -> pd.Series:
    # Qlib's binary layout: float32 little-endian, first value is the index
    # into the calendar where the series starts.
    raw = np.frombuffer(zf.read(f"features/{sym.lower()}/close.day.bin"), dtype="<f4")
    start = int(raw[0])
    values = raw[1:].astype(float)
    return pd.Series(values, index=cal[start:start + len(values)], name=sym)


def build(qlib_zip: str, alpaca_json: str, out_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Write the spliced panel and a source log. Returns the panel."""
    zf = zipfile.ZipFile(qlib_zip)
    cal = pd.DatetimeIndex(pd.to_datetime(zf.read("calendars/day.txt").decode().split()))
    alp = json.load(open(alpaca_json))

    rets = {}
    log = {}
    for sym in ETFS:
        q = _qlib_series(zf, cal, sym).dropna()
        q = q[q > 0]
        a = pd.Series(alp[sym]["c"], index=pd.to_datetime(alp[sym]["d"]), dtype=float)
        rq = q.pct_change().dropna()
        ra = a.pct_change().dropna()
        r = pd.concat([rq[rq.index <= QLIB_END], ra[ra.index > QLIB_END]])
        rets[sym] = r
        log[sym] = {
            "first_return": str(r.index[0].date()),
            "last_return": str(r.index[-1].date()),
            "yahoo_qlib_days": int((r.index <= QLIB_END).sum()),
            "alpaca_days": int((r.index > QLIB_END).sum()),
        }

    rr = pd.DataFrame(rets).sort_index()
    # A total-return index per ETF. A missing day inside an ETF's life (one
    # in UUP's Yahoo history) carries the last value, i.e. a zero return.
    panel = (1 + rr).cumprod()
    for sym in ETFS:
        first = rr[sym].first_valid_index()
        panel.loc[first:, sym] = panel.loc[first:, sym].ffill()
    out_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_dir / PANEL, float_format="%.8f")
    json.dump({
        "splice": f"Yahoo adjusted (Qlib) through {QLIB_END.date()}, Alpaca adjustment=all after",
        "qlib_zip": "github.com/SunsetWolf/qlib_dataset releases v2 qlib_data_us_1d_latest.zip",
        "alpaca_json": "trade.zip: trade/insider_research/etf_prices.json (fetched 2026-08-01)",
        "per_symbol": log,
    }, open(out_dir / SOURCES, "w"), indent=2)
    return panel


def load(data_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Total-return index per ETF. NaN before an ETF existed."""
    p = pd.read_csv(Path(data_dir) / PANEL, index_col=0, parse_dates=True)
    return p[ETFS]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qlib-zip", required=True)
    ap.add_argument("--alpaca-json", required=True)
    ap.add_argument("--out", default=str(DEFAULT_DIR))
    a = ap.parse_args()
    p = build(a.qlib_zip, a.alpaca_json, Path(a.out))
    print(p.notna().idxmax().to_string())
    print(p.index[0].date(), "to", p.index[-1].date(), len(p), "days")
