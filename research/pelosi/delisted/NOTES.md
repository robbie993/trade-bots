# Delisted and renamed stocks: the survivorship gap

Written 2026-10-07. The goal was free daily prices (adj close and volume, 2013 to now) for every stock that was ever top-100 by dollar volume and has since been delisted or acquired.

## What was recovered: renamed tickers (`rename_map.csv`, `renamed_prices.parquet`)

- **The problem:** Yahoo drops a company's old ticker when it changes symbol, but keeps the full history under the new one. These were listed as "no prices" only because of the old symbol.
- **What I did:** matched the missing tickers' company names (from the congress filings) against SEC `company_tickers.json` titles, exact matches only (fuzzy matches were wrong too often), plus a hand list of known renames.
- **Result:** 34 recovered, each with a series back to 2012. Examples:
  - BK → BNY, MMC → MRSH, ABC → COR (Cencora), RDS-A/B → SHEL, COH → TPR, HRS → LHX, ACE → CB;
  - CBG → CBRE, BLL → BALL, HCN → WELL, GPS → GAP, ANTM → ELV, CTL → LUMN, FI → FISV, MPW → MPT;
  - SNE → SONY, ABB → ABBNY, MYL → VTRS, UTX → RTX, DWDP → DD, BHI → BKR, PX → LIN, WLTW → WTW;
  - KORS → CPRI, SYMC/NLOK → GEN, HFC → DINO, FB → META.
- **`renamed_prices.parquet`** is in the same long format as `../data/congress_prices_*` (without raw_close), keyed by the **new** ticker. Join through `rename_map.csv`.
- **Not recoverable this way:**
  - CBS / VIAB / VIAC / PARA: Yahoo's PSKY series starts 2026-07-29.
  - DISCA / DISCK: Yahoo's WBD series currently has a single day. That looks like a 2026 restructuring; the old WBD history is no longer served.
  - SQ: XYZ starts at Block's 2015 listing, which is the true start.
- **Effect on the universe:** after adding these, the ever-top-100 universe in `../fundamentals/` is **unchanged** (324 tickers). None of the renamed names ranked in the top 100 by dollar volume under a symbol we lacked.

## What is still missing: truly delisted companies

- **`known_delisted_large_caps.csv`** lists 58 well-known large caps delisted or acquired since 2014, with how often members traded them. Almost all have no price data:
  - Twitter, Celgene, Activision, Allergan, Xilinx, Alexion, Express Scripts, Time Warner, Aetna, Monsanto, Red Hat, LinkedIn, Yahoo, EMC;
  - VMware, Splunk, Citrix, Cerner, Pioneer Natural, Hess, First Republic, SVB, Discover, Walgreens, Ansys, Juniper and others.
- **`congress_traded_without_prices.csv`** covers all 1,618 member-traded symbols that still have no price, with trade counts. Many are small caps, foreign lines or junk strings.
- **Which were top-100 by dollar volume** can't be computed without their prices. From general knowledge, the likely members include Celgene (2014–19), Allergan, Twitter, Activision, Express Scripts, Time Warner, Monsanto, LinkedIn, Yahoo, EMC, Xilinx (2020–21), Pioneer and First Republic/SVB (2023). Expect roughly 15–30 names, each for part of the period.

## Sources tried

| Source | Result |
|---|---|
| Yahoo (yfinance) | Delisted tickers return 404 "quote not found". Renamed ones are recoverable as above. |
| Stooq CSV download | Now behind a JavaScript bot check, so it can't be scripted. |
| Hugging Face `paperswithbacktest/Stocks-Daily-Price` (7,764 symbols, 1962–2026, includes delisted) | **Gated behind a paid subscription.** Not used. |
| Hugging Face `HaiwenWang/NASDAQ_10y…` | Current NASDAQ listings only; no delisted names. |
| Hugging Face `no-ry/world-stock-prices…` | A few dozen world brands. Downloads also timed out from this PC (the Xet CAS server is unreachable). |
| Nasdaq.com historical API | No usable response. |
| GDELT | (for news, earlier) API blocked from this PC. |

## Options to close the gap (need a decision)

1. **Alpaca historical bars, using the project's existing Alpaca data access.** Alpaca's data covers delisted US symbols from about 2016. It is free with the account the village already has, but it uses Robbie's keys, so it needs his OK. It would not cover 2013–2015.
2. **A free API key from Tiingo, EOD Historical Data or Polygon.** Tiingo's free tier serves delisted tickers back decades (with a monthly symbol limit). Robbie would sign up and add the key to `.env` himself; it never goes into chat.
3. **Accept and measure the bias.** Run the most-traded-stocks backtest as is, and report it as an upper bound. Re-run with the known delisted names forced in at their historical weights once prices exist.
