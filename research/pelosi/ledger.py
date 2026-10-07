"""Pelosi economic-event ledger built from the point-in-time congress dataset.

Every row of austin-starks/congressional-stock-trades (HF) for filer Pelosi is
classified, amendments and duplicate rows are folded into one economic event,
and option details are parsed from the free-text description.

Classification (doc sections 7-8):
  buy_stock   Purchased N shares
  buy_call    Purchased N call options (bullish signal on the underlying)
  exercise    Exercised calls: lifecycle of an earlier buy_call, not a new signal
  sell_stock  Sold N shares
  sell_call   Sold call options (closing an earlier buy_call)
  gift        Contribution of shares to a charity/DAF: not a market signal
  corp        Spinoffs, exchanges, liquidations: not a signal
  private     No listed ticker (LLCs, real estate, funds): not tradeable
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"

# Renamed tickers (dataset ticker -> price-file ticker).
TICKER_MAP = {"FB": "META", "SQ": "XYZ"}
# Companies whose old price history is not in the price file (delisted or the
# ticker now belongs to another company). Kept in the ledger, priced as missing.
NO_PRICE = {
    ("SUNE", None): "SunEdison went bankrupt in 2016; ticker now belongs to SUNation",
    ("HTZ", "pre2021"): "Old Hertz Global went bankrupt in 2020; history before 2021-07 missing",
    ("WORK", None): "Slack was bought by Salesforce in 2021; history missing",
    ("AA", "pre2016"): "Old Alcoa split in 2016; today's AA is a different company",
    ("DOW", "pre2019"): "Old Dow Chemical merged in 2017; today's DOW listed 2019",
    ("BRCM", None): "Old Broadcom bought by Avago 2016",
    ("ELX", None): "Emulex bought 2015", ("ENTR", None): "Entropic bought 2015",
    ("BCOR", None): "Blucora history incomplete", ("SFLY", None): "Shutterfly taken private 2019",
    ("BFET", None): "BF Enterprises, OTC liquidation",
}

NUM = r"([\d,\.]+)"


def _num(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace(",", "")
    if re.fullmatch(r"\d+\.\d{3}", s):        # "1.000" typo for 1,000
        s = s.replace(".", "")
    try:
        return float(s.rstrip("."))
    except ValueError:
        return None


def parse_comment(c: str) -> dict:
    c = c if isinstance(c, str) else ""
    out = {}
    m = re.search(r"(\d[\d,\.]*)\s+(?:call\s+)?options", c, re.I)
    if m:
        out["contracts"] = _num(m.group(1))
    m = re.search(r"strike price of \$" + NUM, c, re.I)
    if m:
        out["strike"] = _num(m.group(1))
    m = re.search(r"(?:expiration date of|expiring)\s+(\d{1,2}/\d{1,2}/\d{2,4})", c, re.I)
    if m:
        out["expiry"] = pd.to_datetime(m.group(1), format="mixed")
    m = re.search(r"(\d[\d,\.]*)\s+(?:shares|units)", c, re.I)
    if m:
        out["shares"] = _num(m.group(1))
    return out


def classify(row) -> str:
    c = row.comment.lower() if isinstance(row.comment, str) else ""
    a = row.action
    if not isinstance(row.ticker, str) or not row.ticker:
        return "private"
    if row.assetTypeCode in ("AB", "PS") and row.ticker != "AB":
        return "private"
    if "contribution" in c:
        return "gift"
    if a == "exchange" or "liquidation" in c or "exchanged" in c or "spinoff" in c:
        return "corp"
    if "exercised" in c:
        return "exercise"
    if "option" in c:
        return "buy_call" if a == "purchase" else "sell_call"
    if a == "purchase":
        return "buy_stock"
    if a == "sale":
        return "sell_stock"
    return "corp"


def amount_mid(lo, hi) -> float:
    if pd.isna(lo):
        return float("nan")
    if lo < 100:            # dataset quirk: bracket lost, value is a row count
        return float("nan")
    if pd.isna(hi) or hi <= lo:
        return lo * 1.5
    return (lo + hi) / 2


def load_raw() -> pd.DataFrame:
    h = pd.read_parquet(DATA / "congress_hf.parquet")
    p = h[h.filerLast.str.contains("Pelosi", case=False, na=False)].copy()
    return p


def build() -> pd.DataFrame:
    p = load_raw()
    p["kind"] = p.apply(classify, axis=1)
    det = p.comment.apply(parse_comment).apply(pd.Series)
    p = pd.concat([p, det], axis=1)
    p["tdate"] = pd.to_datetime(p.transactionDate)
    # availableAt is stamped 23:59:59 New York time of the publication day
    # (03:59/04:59 UTC next day). The first session a copier can act on is the
    # next trading day; we enter at that day's close.
    p["public_date"] = (p.firstAvailableAt - pd.Timedelta(hours=5)).dt.normalize()
    p["mid_usd"] = [amount_mid(lo, hi) for lo, hi in zip(p.amountLow, p.amountHigh)]
    p["px_ticker"] = p.ticker.map(lambda t: TICKER_MAP.get(t, t) if isinstance(t, str) else t)

    # Amendments/duplicates: same ticker, date, kind and size are one economic
    # event; keep the first time it became public, remember every source doc.
    key_num = p.shares.fillna(p.contracts).fillna(-1)
    p["econ_key"] = (p.px_ticker.fillna(p.assetDescription) + "|" + p.tdate.dt.strftime("%Y-%m-%d")
                     + "|" + p.kind + "|" + key_num.astype(str) + "|" + p.strike.fillna(-1).astype(str))
    p = p.sort_values("firstAvailableAt")
    docs = p.groupby("econ_key").sourceDocId.agg(lambda s: ",".join(sorted(set(map(str, s)))))
    first = p.drop_duplicates("econ_key", keep="first").copy()
    first["source_docs"] = first.econ_key.map(docs)
    first["n_filings"] = first.source_docs.str.count(",") + 1
    first["lag_days"] = (first.public_date - first.tdate).dt.days
    # Reconciliation with the official PDFs: the 2014 HTZ/DIS rows only say
    # "Purchase of N Options"; the amended PTR 20003320 (2015-07-07) gives the
    # contract terms. Fill them in from there.
    for t, k in (("HTZ", 22.0), ("DIS", 90.0)):
        m = (first.ticker == t) & (first.kind == "buy_call") & (first.tdate.dt.year == 2014)
        first.loc[m, "strike"] = k
        first.loc[m, "expiry"] = pd.Timestamp("2016-01-15")
        first.loc[m, "source_docs"] = first.loc[m, "source_docs"] + ",20003320(pdf)"
    cols = ["eventId", "econ_key", "kind", "tdate", "public_date", "lag_days", "owner", "action",
            "ticker", "px_ticker", "assetTypeCode", "amountLow", "amountHigh", "mid_usd", "contracts",
            "strike", "expiry", "shares", "assetDescription", "comment", "source_docs", "n_filings",
            "sourceUrl"]
    return first[cols].sort_values(["tdate", "ticker"]).reset_index(drop=True)


if __name__ == "__main__":
    led = build()
    raw = load_raw()
    print("raw rows", len(raw), "economic events", len(led))
    print(led.kind.value_counts().to_string())
    print("merged duplicates:", (led.n_filings > 1).sum())
    print(led[led.n_filings > 1][["tdate", "ticker", "kind", "source_docs"]].to_string())
    out = DATA.parent / "out"
    out.mkdir(exist_ok=True)
    led.to_csv(out / "pelosi_ledger.csv", index=False)
