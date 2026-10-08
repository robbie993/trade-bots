# Public channels: did anything public point to Pelosi's trades?

Pulled and tested 2026-10-07 on Robbie's PC. Public data only. The scripts are in `scripts/`; the tests are in `tests/`.

## Data

| File | Rows | Source and notes |
|---|---|---|
| `committees.csv` | 36,393 | Committee and subcommittee seats for every House and Senate member, 113th–119th Congress. **113–115:** Charles Stewart III's assignment data (MIT; `raw/stewart_*`), with dates and roles (chair, ranking, leadership codes). **113–119:** 210 git snapshots (2012-11 to 2026-09) of `unitedstates/congress-legislators` `committee-membership-current.yaml`, giving bioguide ID, rank, title, and first/last seen (snapshot resolution, so `end` is the next snapshot after last seen). Pelosi appears only as leadership. |
| `contracts.csv`, `contracts_map.csv` | 5,549 | USAspending prime contract awards of **$10M or more** with activity in FY2014–FY2026, for Pelosi's tickers plus the 100 tickers members bought most since 2014 (126 tickers; 48 have awards). Matching rule: the recipient **or** its USAspending parent starts with a company alias, plus a few explicit subsidiaries (Optum, Oracle Health/Cerner, CA Inc., Cellco, Cepheid, Ceradyne, FlightSafety). The Bell Boeing JV is excluded. `amount` is the award's total; `action_date` is the base obligation date. `contracts_map.csv` lists every recipient name and whether it was kept. |
| `lobbying.csv.gz` | 42,544 | Senate LDA filings 2014–2026 for 107 of the companies: 3,650 client records, because each lobbying firm files under its own client record. One row per filing: ticker, client, registrant, year, quarter, filing type, `amount` (income for outside firms, expenses for in-house), issue codes, bill numbers found in the activity text (37% name one), government entities, and the description. **FB/META and GOOG/GOOGL appear under both tickers; dedupe by `filing_uuid`.** |
| `laws.csv` | 2,165 | Every public law of the 113th–119th Congress (govinfo PLAW and BILLSTATUS): bill, titles, policy area, subjects, committees, introduced date, passed House, passed Senate, presented, became law, sponsor. It adds `companies_named` (only 1 law names a listed company) and `sector_*` keyword flags: tech 239, health 271, energy 192, finance 315, defense 535. The 119th is partial (118 laws) because govinfo publishing lags. |
| `hearings.csv.gz` | 15,537 | Every hearing published on govinfo 2013–2026 (MODS): held dates, chamber, congress, committees, title, witnesses, plus companies matched in witness affiliations (391 hearings) and titles (41), with the matched name in `matched_names_*`. Subsidiary names are not mapped across acquisition dates. |
| `insider_trades.csv.gz` | 264,052 | SEC Form 3/4/5 insider transactions (quarterly bulk sets 2014q1–2026q3), non-derivative, for the 125 issuers. Includes transaction code (P = open-market buy, S = sale), shares, price, value, owner and title, and filing date. A few transaction dates are typos (1999, 2027). |
| `options_volume_occ.csv.gz`, `options_volume_daily.csv` | 1.70M / daily | OCC volume query, **2024-10 to 2026-09 only** (the OCC serves 24 months), for 38 of Pelosi's still-listed tickers plus SPY/QQQ. Split by account (C = customer, F = firm, M = market maker), call/put and exchange. **No free source has per-ticker option volume or open interest back to 2014**: the OCC caps at 2 years, CBOE's free data is current-only, and daily open-interest downloads returned errors. |
| `wiki_pageviews.csv.gz` | 159,283 | Wikipedia daily views (user agents) for 44 of her companies' articles, 2015-07 to now. Used as the attention proxy because **GDELT's API is blocked from this PC**. Its raw files are reachable, but there are ~380k quarter-hour files. |

## Tests (`tests/`)

**Method.** For each channel and each of her directional trades (2014-06 to 2026-06; option exercises and gifts excluded):
- count the channel's events in the 90 days (30 for attention) before and after the trade;
- compare with that ticker's own base rate;
- compare with 2,000 placebos: random dates over the whole window, and a **local placebo** that moves each trade 31–365 days so its era is kept.

| Channel | Before her buys | After her buys | Before/after her sales |
|---|---|---|---|
| Contracts $10M+ | normal (1.0×) | normal | more before sales (2.9×, 7 tickers, p≈0.01–0.04) |
| Insider open-market buys | normal | normal | normal |
| Insider sales | **1.7× more** (p 0.004–0.02) | 1.4× (ns) | fewer (0.45×) |
| Option volume, customer calls, last 2 yrs (15 buys) | 20% of buys >1.5× normal vs 24% base | — | none |
| Wikipedia attention spikes | 1.4× (ns) | 3.1× (p≈0.01) → **gone after removing her disclosure week** (p=0.55) | fewer |
| Lobbying (filings, $, bill-specific) | normal (0.8–0.9×) | normal | 1.3–1.6× before **and** after (p 0.01–0.08) |

**How to read it.**
- **Nothing public moved ahead of her buys.** Insiders were *selling* into them, which fits her habit of buying after a run-up: insiders sell into run-ups too.
- **Attention after her buys is her own disclosure,** the "Pelosi effect". The next-day reaction is in `../precursors/disclosure_reaction.csv`: smaller names +5.0% vs SPY (Tempus +35%), megacaps +1.1%.
- **The sale-side lobbying and contract results** are symmetric (before and after), weak against the local placebo, and come from about 40 tests. They read as era and company composition (her sales cluster in heavy-lobbying periods), not timing.
- **No laws or hearings test was run here.** The analysis thread has a legislative test: trades with a legislative connection did worse.
