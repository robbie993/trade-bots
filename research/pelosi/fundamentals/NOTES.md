# Fundamentals for the most-traded-stocks study

Pulled 2026-10-07 on Robbie's PC. The analysis happens elsewhere; this folder holds data only.

## Universe: `universe.csv`

- **Rule:** every ticker that ranked in the top 100 by 63-day average dollar volume (close × volume) at any month-end from 2014-01 to 2026-10. That is 154 month-ends, and 324 tickers qualified.
- **Ranking file:** `_top100_monthly_raw.csv` has the monthly ranks (`ym`, `ticker`, `dv63`, `rk`). Note that `close` is split-adjusted, so for dollar volume that is fine only because volume is split-adjusted the same way.
- **Funds dropped:** `is_fund` flags 65 ETFs (yfinance `quoteType`), leaving **259 operating companies**. Each has an SEC CIK from `company_tickers.json`.
- **Survivorship (important):** prices come from `../data/congress_prices_*`, which only holds tickers some member traded and Yahoo still prices.
  - Delisted or acquired former giants are **not** in the universe: Twitter, Celgene, Activision, Allergan, Xilinx, Abiomed, Splunk and similar.
  - SEC's `company_tickers.json` also lists only current tickers.
  - So a backtest on this universe starts with a survivor tilt. Rebuilding the true historical top 100 would need a delisted-price source.

## `fundamentals.csv.gz` (297,537 rows, 258 companies)

- **Source:** SEC XBRL companyfacts (`data.sec.gov/api/xbrl/companyfacts`).
- **Columns:**
  - `ticker, cik, taxonomy, tag, tag_std, value, unit`;
  - `period_start, period_end, fiscal_year, fiscal_period, form`;
  - **`filed`**, the date the number became public: use it for point-in-time joins;
  - `frame, accession`.
- **Tags:**
  - EarningsPerShareDiluted, NetIncomeLoss;
  - Revenues and RevenueFromContractWithCustomerExcludingAssessedTax (companies switched to the latter around 2018);
  - StockholdersEquity;
  - CommonStockSharesOutstanding and dei:EntityCommonStockSharesOutstanding;
  - OperatingIncomeLoss;
  - NetCashProvidedByUsedInOperatingActivities;
  - PaymentsToAcquirePropertyPlantAndEquipment.
- **Foreign filers:** 10 report in IFRS (AZN, BNTX, BTI, CGC, IREN, NOK, PBR, SPOT, TSM, VOD). Their `ifrs-full` tags are kept with `tag_std` mapped to the names above: ProfitLoss → NetIncomeLoss, Revenue → Revenues, Equity → StockholdersEquity, and so on. Values are in the filer's reporting currency; check `unit`.
- **Things to handle in analysis:**
  - The same fact repeats in later filings as a comparative. Use the first `filed` per (tag, period_start, period_end) for point-in-time data.
  - Duration facts (income, cash flow) mix quarterly, year-to-date and annual periods. Use `period_start`/`period_end` (or `frame`) to get true quarters.
  - Coverage by tag (companies): EPS 253, NetIncome 257, StockholdersEquity 253, OCF 258, OperatingIncome 235, Capex 215, Revenues 187 plus RevenueFromContract 196, CommonStockSharesOutstanding 188, dei shares 217.
  - One equity has no facts for these tags.
  - Filed dates run from 2009-04 to 2026-10.

## Earnings dates

- **`sec_filing_dates.csv`** (23,021 rows, 258 companies) lists every 10-Q, 10-K, 20-F and 40-F (and their /A amendments) filed since 2013, plus every 8-K with **item 2.02** (results of operations). That 8-K is the earnings press release, usually filed on announcement day, ahead of the 10-Q. It is the best free point-in-time earnings date.
- **`earnings_all.csv`** (18,901 rows, all 259) is yfinance `get_earnings_dates(limit=60)`: date and time, EPS estimate, reported EPS and surprise. It goes back about 15 years; future rows are scheduled dates.
