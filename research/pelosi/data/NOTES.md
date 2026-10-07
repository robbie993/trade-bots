# Pelosi backtest data: notes

Gathered 2026-10-05 to 10-07 on Robbie's PC, because the cloud thread can't reach these sites. This folder has data only. There is no analysis here.

## Congress trades

| File | Rows | Notes |
|---|---|---|
| `congress_hf.parquet` | 181,647 | HF `austin-starks/congressional-stock-trades`, table `political_trade_events` (the table `src/trading/congress.py` reads). All members (465 filerKeys), all 28 columns, including 192 superseded rows. transactionDate runs 2004-03-03 to 2026-09-23 (526 null); availableAt runs 2012-07-26 to 2026-10-02. |
| `congress_hf_trades.parquet` | 186,340 | Same dataset, table `political_trades`: raw per-row parse with printed vs resolved ticker, `capGainsOver200`, notification/filing dates. |
| `congress_hf_filings.parquet` | 10,825 | Same dataset, table `political_filings`: one row per filing, with parse method and failure reason. |
| `hf_raw/` | | Dataset README, METHODOLOGY, LICENSE_DATA and snapshot.json. Read METHODOLOGY for how availableAt is defined. |
| `congress_lake.csv.gz` | 181,647 | `npx congressional-disclosures@latest download --sqlite`, table `political_trade_events`, all rows. Columns are snake_case. It is the same data as the HF table: the eventId sets match exactly. |
| `lake_schema.txt` | | CREATE statements for every SQLite table, plus row counts. |

Lake notes: Node isn't installed on the PC, so I ran a portable Node 22.20 from a temp folder. The README's bare `--sqlite` fails on Windows with "unable to open database file". Passing an explicit Windows path to `--sqlite` worked.

**Pelosi in the dataset:** 216 rows, all House and all Nancy, 0 superseded, transactions 2013-11-26 to 2026-07-28, 45 distinct tickers (18 rows have no ticker). Asset types: ST 94, OP 55, none 45, AB 14, OL 5, OT 2, PS 1. The rows come from 63 source documents, all of them in the Clerk's PTR list. Rows by availableAt year: 2014 16, 2015 5, 2016 9, 2017 7, 2018 17, 2019 10, 2020 33, 2021 27, 2022 23, 2023 20, 2024 10, 2025 12, 2026 27.

**The filing's own "why":** the `comment` column is filled on 207 of the 216 Pelosi rows. It carries the filer's description, for example "Purchase of 50 Options", strikes and expiries, exercises, and "Contribution of shares … to The Paul & Nancy Pelosi Charitable Foundation". Use it to split real directional bets from gifts, option exercises and rebalancing.

## House Clerk

- `pelosi_clerk_index.csv` has 89 rows where Last == 'Pelosi', taken from the 2012–2026 `{YEAR}FD.zip` XML indexes. FilingType counts: P 67, O 15, A 5, X 2. No year failed.
- `ptr_pdfs/` holds all 67 PTR PDFs (P filings), none missing. Four of them have no rows in the dataset (67 PTRs against 63 source docs). They are worth checking by hand.
- `ptr_text.csv` (extra) has the extracted text of each PDF, keyed by DocID. One PDF is a scan with no text layer.

## Prices (yfinance 1.7.0, auto_adjust=False, actions=True, from 2012-01-01)

Format: long, with columns date, ticker, open, high, low, close, adj_close, volume, dividends, splits, raw_close.

**Correction:** Yahoo's `close` is already adjusted for splits even with auto_adjust=False; it only leaves out dividends. `raw_close` is the price as it actually traded that day: close times every split ratio after that date. Use `raw_close` to compare against option strikes. Example: NVDA on 2021-06-03 has close 16.97 and raw_close 678.79.

- `pelosi_prices.parquet` has 155,982 rows for 51 tickers, 2012-01-03 to 2026-10-05.
  - These failed: BFET, BRCM, ELX, ENTR, KRUZ, SFLY, SQ, WORK. All are delisted, acquired or renamed. KRUZ (the Republican-trades ETF) has closed.
  - I added these replacements: XYZ (Block, formerly SQ) and META (formerly FB).
  - **Warning: ticker `FB` in this file is NOT Meta.** Yahoo now maps FB to a different security that only starts on 2025-06-26. Use META for any FB trade.
- `congress_prices_{YYYY}.parquet` (2013 through 2026) holds the prices for every ticker any member traded since 2014-01-01, starting 2013-06-01. Together the files are over the 90 MB limit, so they are split by year (each at most 13.4 MB, zstd).
  - 9,953,085 rows and 3,400 tickers, from 2013-06-03 to 2026-10-07.
  - 5,028 ticker strings were requested in batches of 200.
    - The tickers traded since 2019 (3,680) were fetched first. That pass lost about 1,100, mostly to Yahoo rate-limiting. A retry two days later and a cleanup of malformed strings (for example "-- RTN") recovered about 300.
    - The 1,348 tickers traded only from 2014 to 2018 were fetched next. 823 of them failed, and a retry recovered none, so these are real misses, not rate limits.
  - `congress_prices_failed.txt` lists the 1,626 that are still missing. Most are delisted or acquired companies (ABMD, AGN, ATVI-style names), foreign or OTC lines, or junk strings like "AIV AIRC".
  - **This is survivorship bias:** trades in companies that later went away have no prices here. A cross-member backtest should report how many trades it dropped for missing prices.

## Earnings

`pelosi_earnings.csv` has 2,494 rows for 39 tickers, from `get_earnings_dates(limit=60)`, covering 2000-02 to 2026-12 (future rows are scheduled dates). Columns: ticker, Earnings Date, EPS Estimate, Reported EPS, Surprise(%). These had none: BCOR, BFET, BRCM, ELX, ENTR, FB, SFLY, WORK. Use META and XYZ in place of FB and SQ.

## Not gathered (the "why" beyond the filing)

These sources aren't fetched yet: committee assignments, bill votes and timing (for example the CHIPS Act around the NVDA calls), and news around each trade date. Ask if the analysis needs them.

## Trade-by-trade table: `pelosi_trades.csv` (216 rows × 71 columns)

Each row is one of Pelosi's disclosed trades, with all the dataset columns plus:

- **Filing:** `FilingType` and `clerkFilingDate` from the Clerk index.
- **Timing:** `tradeDate`, `disclosedAt` (availableAt in New York time), and `lagDays` from trade to disclosure.
- **Forward returns, two ways:**
  - `trade_*` enters at the adj_close of the first session on or after the trade date. That is the member's own timing, which no one outside could copy.
  - `disc_*` enters at the adj_close of the first session after the disclosure date. That is what a copier could actually do.
  - Each has `_ret{1,5,20,60,120,250}d` (raw return) and `_xs…d` (return minus SPY over the same window).
- **Earnings:** `nextEarnings` and `daysToEarnings` from the trade date.
- **Parsed from her comments:** `optContracts`, `optRight` (call/put), `optStrike`, `optExpiry`, `shares`, and `nonDirectional`. The last one is 'gift' (15 rows: charity contributions) or 'exercise' (32 rows: option exercises), so these can be left out of a signal test.

Returns use the underlying stock, never the option. FB is priced from META and SQ from XYZ. 26 rows have no price (no ticker, a delisted name, or an LLC or other non-stock asset).

## Fixes made on 2026-10-07

- **Reused tickers:** HTZ (2014–15, old Hertz), DOW (2014, Dow Chemical) and BCOR now belong to different companies on Yahoo. The trade table had picked up their first available price years later. Those 8 rows now have no `trade_*` or `disc_*` values (182 priced rows instead of 190).
- **Amendments:** the 4 "missing" PTRs are 3 amendments, whose rows are folded into the original filings, plus 20035553 (filed 2026-10-02), a $500k–$1M real-estate LLC (REOF XXX, 225 Bush St). That one is newer than the dataset snapshot. No stock trades are missing.
- **Scanned PTR:** 8214491 is the scanned 2013 filing for the Active Network cash-out. It matches its dataset row.
- **Annual and amendment reports:** `annual_pdfs/` holds the 15 annual reports, 5 amendments and 2 extension filings. `annual_text.csv` has their text, which lists full holdings, including the LEAPS positions and private stakes such as the Forge/Databricks fund.

## Fix made on 2026-10-07 (evening)

- **`congress_prices_failed.txt` rewritten** as "requested minus priced": 1,628 entries.
  - A later retry recovered TEV, which is now merged into the yearly price files.
  - Raw strings like `-- DIS` stay listed as failed. DIS itself is priced. Split the file on newlines, not whitespace.
- **Renamed tickers:** some missing names were ticker changes (BK → BNY, MMC → MRSH, ABC → COR and more). Their prices are in `../delisted/renamed_prices.parquet`. See `../delisted/NOTES.md`.
