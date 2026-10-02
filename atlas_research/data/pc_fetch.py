"""Rebuild the ETF panel from one source, on a machine that can reach Yahoo.

The cloud build splices two sources (see `prices.py`). This script pulls the
same 14 ETFs from Yahoo alone (dividend-adjusted closes, 2000 to today) and
the 3-month T-bill series from FRED, so the result can be re-run on a single
clean source and the splice checked against it.

    pip install yfinance
    python -m atlas_research.data.pc_fetch --out C:\\dev\\atlas_data
    set ATLAS_DATA_DIR=C:\\dev\\atlas_data
    python -m atlas_research.run_core discover

Needs no API keys.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pandas as pd

from .prices import ETFS, PANEL, SOURCES

FRED_TB3MS = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TB3MS"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import requests
    import yfinance as yf

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = yf.download(ETFS, start="1999-12-01", auto_adjust=True, progress=False)["Close"]
    data = data[ETFS].dropna(how="all")
    panel = data / data.bfill().iloc[0]
    panel.to_csv(out / PANEL, float_format="%.8f")
    tb = pd.read_csv(io.StringIO(requests.get(FRED_TB3MS, timeout=60).text))
    tb.to_csv(out / "tb3ms.csv", index=False)
    json.dump({"splice": "none: Yahoo only (yfinance auto_adjust)",
               "fetched_rows": len(panel), "last_day": str(panel.index[-1].date()),
               "tbill": "FRED TB3MS saved to tb3ms.csv; compare with data/rates.py"},
              open(out / SOURCES, "w"), indent=2)
    print(panel.notna().idxmax().to_string())
    print("rows", len(panel), "to", panel.index[-1].date())


if __name__ == "__main__":
    main()
