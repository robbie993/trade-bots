---
pretty_name: Congressional Stock Trades
license: other
license_name: us-congressional-disclosure-restrictions
license_link: https://huggingface.co/datasets/austin-starks/congressional-stock-trades/blob/main/LICENSE_DATA.md
language:
- en
tags:
- finance
- politics
- congress
- stock-trading
- point-in-time
size_categories:
- 100K<n<1M
configs:
- config_name: political_trade_events
  default: true
  data_files:
  - split: train
    path: "data/political_trade_events/*.parquet"
- config_name: political_trades
  data_files:
  - split: train
    path: "data/political_trades/*.parquet"
- config_name: political_filings
  data_files:
  - split: train
    path: "data/political_filings/*.parquet"
---

# Congressional Stock Trades

An audited, point-in-time dataset of U.S. House and Senate periodic transaction
reports from the official House Clerk and Senate eFD sources.

[GitHub source and documentation](https://github.com/austin-starks/congressional-disclosures) ·
[npm package](https://www.npmjs.com/package/congressional-disclosures) ·
[Methodology](./METHODOLOGY.md)

Current snapshot: **10,825 filings · 186,340 reported transaction rows · 181,647 reconciled event versions**. The latest House filing date is `2026-10-01`; the latest Senate filing date is `2026-10-01`. Source manifests were last checked at `2026-10-05T02:13:04.788Z`. The target refresh interval is 20 hours.

## Choose the right table

- **political_trade_events** (default): use this to count or aggregate trades. Repeated reports fold into one event; amendments add point-in-time versions.
- **political_trades**: one row per transaction printed on a successfully extracted filing. The same economic trade can appear more than once.
- **political_filings**: one row per official document, including 1 filings with a named extraction failure. Failed filings do not contribute partial or unverified trade rows.

## Members of Congress

Every table carries the filer's identity, matched against the public-domain
[congress-legislators](https://github.com/unitedstates/congress-legislators) data
(commit `577ca04282d14688cb14f6559eb27b52bdc50dfc`):

- `memberId` is the member's Bioguide ID, one per person across name spellings and both chambers.
- `displayName` is the member's official name; `filerFirst` and `filerLast` keep the name as filed.
- `filerKey` is `member:<memberId>` for members.
- `identitySource` is `legislators`, `override` (a reviewed case such as a married name), `non_member`, or `unresolved`.

Only members of Congress have events: 466 members in this snapshot. 2 filings by people who never served (committee staff, a candidate's notice) stay in the filing and trade tables with `identitySource = 'non_member'`.

## Point-in-time semantics

`availableAt` is the information boundary. Filter `availableAt <= as_of`; never use
`transactionDate` as the date the public learned about a trade. For event versions,
also require `supersededAt IS NULL OR supersededAt > as_of`.

## Reproducibility and freshness

The Parquet files are byte-for-byte mirrors of the manifest-selected NexusTrade lake
shards. [snapshot.json](./snapshot.json) records source manifest ETags, object ETags,
row counts, file sizes, SHA-256 checksums, audit findings, and freshness timestamps.
The mirror runs after successful lake publication; a Hugging Face outage leaves the
last verified public snapshot intact and is retried on the next disclosure pass.

## Download a queryable SQLite database

The npm package can download the current audited snapshot and materialize all three
tables into SQLite without Docker, Python, DuckDB, API keys, model credentials, or
OCR credentials. It checks available disk space separately for the compressed files
and the database, verifies every file's published byte size and SHA-256, and validates
the final row counts:

```bash
npx congressional-disclosures@latest download --sqlite
npx congressional-disclosures@latest status \
  --db ./congressional-stock-trades/congressional-disclosures.sqlite
```

Run `download` without `--sqlite` when you only want the Parquet files.

## Build from the official sources

The open-source [congressional-disclosures](https://www.npmjs.com/package/congressional-disclosures)
package discovers official House and Senate filings, handles encrypted/scanned PDFs,
extracts and validates rows, and writes the same three-table model locally:

```bash
npx congressional-disclosures@latest doctor
npx congressional-disclosures@latest sync --db ./congress.db --since 2024 --accept-senate-terms
npx congressional-disclosures@latest audit --db ./congress.db
```

## Limitations

- Values are reported ranges, not exact trade sizes.
- Filing dates lag transaction dates, sometimes substantially.
- Extraction uses OCR and schema-bound model consensus; hard scans and filer errors can
  still fail. Failures remain visible rather than being silently dropped.
- A few scanned filings carry rows read or corrected by hand. [METHODOLOGY.md](./METHODOLOGY.md)
  lists each one and what was done, and each such trade says so in `comment`.
- Names, tickers, amendments, and duplicate filings require care; prefer the event table
  for economic counts.
- This is public-record data, not investment advice.

## Sources and use restrictions

Source documents come from the official House Clerk disclosure portal and Senate eFD.
The records are publicly accessible, but 5 U.S.C. 13107(c) restricts how financial
disclosure reports may be obtained or used. Read [LICENSE_DATA.md](./LICENSE_DATA.md)
before using the dataset. The extraction software is separately MIT-licensed.
