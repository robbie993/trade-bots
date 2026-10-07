# Can we pick what Pelosi picks before she does?

This is research only: a test on past data, built from public filings and public prices. All the code is in `scripts/`; the inputs are in `../data/` (see `../data/NOTES.md`). Run on 2026-10-07.

## The short answer

- **Which stocks:** partly predictable. She buys large-cap tech, often names she already holds, after they have risen for a few months. A one-line rule ("the biggest stocks by dollar volume") ranks her actual pick a median 13th out of 500. Nothing I added beat that rule.
- **Which day or which single name:** not predictable from public data.
- **"How she gets the info":** the trades don't look like trading on upcoming news. They look like a long-term, leveraged megacap-tech portfolio that is rolled on a calendar. Any edge is in how the account is built, not in the timing.
- **Copying her after she discloses** earned 26%/yr against 19.5% for QQQ. Without Nvidia it earned 19.3%, about the same as QQQ but with more volatility and deeper drops.

## 1. What her buys look like

79 directional stock or call purchases from 2014 to 2026, leaving out 32 option exercises and 15 gifts to the family foundation (`events.csv`, `option_structure.csv`).

- **Names:** AAPL, NVDA, AMZN, MSFT, GOOGL, CRM, DIS, PYPL, META, PANW, AVGO, and AB (AllianceBernstein units). About half the buys (49%) are in names she had already disclosed.
- **Options:**
  - Every option she bought was an in-the-money call. The median strike is 70% of the stock price (interquartile range 56–84%), with a median of 12.5 months to expiry. 47% of them expire in January.
  - That is a cheaper way to own the stock for a year. Someone who knew about upcoming news would buy cheap, short-dated out-of-the-money calls; there are none here.
  - The calls are exercised at expiry (Jan, Mar, Jun) and the position is rolled.
- **Timing:**
  - Buys cluster in Dec/Jan, Feb and May–Jul. 40% of buy days fall within −5 to +45 days of a January or June options expiration, against a 28% base rate. That is roll season.
  - Earnings don't matter: the days-to-next-earnings percentile is 0.57 against 0.50 for a random pick.
- **Holdings** (annual reports in `../data/annual_text.csv`):
  - $5–25M stakes each in AAPL, GOOGL, AMZN, MSFT and AVGO;
  - LEAPS on top of those;
  - private stakes, such as Forge Investments (a fund holding Databricks) and real-estate LLCs;
  - Paul Pelosi's firm, Financial Leasing Services.

## 2. Price path around her trades (`event_study.csv`)

Figures are cumulative return versus SPY, measured from the close before the trade.

| Days from trade | −120 | −60 | −20 | −5 | +5 | +20 | +60 | +120 | +250 |
|---|---|---|---|---|---|---|---|---|---|
| **Buys** (mean) | stock ran up 6.9% | 2.2% | −2.8% | −1.2% | +0.5% | +1.5% | −0.2% | +4.1% | **+15.2%** (median 13.9%) |
| **Sales** (mean) | stock fell 7.4% | 5.3% | | | | +3.2% | +5.3% | +10.9% | **+20.9%** |

- **Buys:** she buys after the stock has risen about 7% more than SPY over six months, often on a short dip in the last week. There is no jump after the trade; the gain builds slowly over a year. Trading on upcoming news would show the opposite.
- **Sales:** what she sells goes on to beat SPY by about 21% over the next year. Her sales are not a signal to sell or short. They look like tax, rebalancing or gift moves.

## 3. Ranking test: could you have named her pick in advance?

**Setup:**
- At each of her 67 buy days (2014–2026), I ranked the roughly 500 most-traded US stocks. The universe for each month comes from the previous month's 60-day dollar volume, taken from every ticker any member traded.
- I scored each stock on information public the day before her trade:
  - momentum over 5, 20, 60, 120 and 250 days versus SPY;
  - distance from the 52-week high, volatility, volume spike and dollar volume;
  - other members' disclosed buys and sells in the last 30 and 90 days;
  - her own earlier disclosures;
  - analyst upgrades, downgrades and price-target raises in the last 30 and 90 days (yfinance, 944 tickers);
  - days to and since earnings.
- The model is a conditional logit (a choice-among-alternatives model), trained only on earlier years and tested on each later year (`walkforward*.csv`, 53 test events).

| Ranker | Median rank of her pick | In top 5 | Top 10 | Top 25 | Top 50 |
|---|---|---|---|---|---|
| Biggest by $ volume (one line) | **13** | 23% | 43% | 59% | **74%** |
| Names she already disclosed | 15.5 | 23% | 43% | 53% | 55% |
| Model: price only | 38 | 17% | 23% | 40% | 64% |
| Model: price + other members | 21 | 26% | 38% | 57% | 64% |
| Model: + her history | 15 | 25% | 45% | 57% | 64% |
| Model: + analysts and earnings | 27 | 25% | 42% | 49% | 62% |

What the fitted coefficients show, with all the data and a bootstrap (`clogit_coefs*.csv`):
- **She favours:** names she already disclosed (z≈5), size (z≈3.4), 6- to 12-month momentum (z≈2–3.5) and higher volatility (z≈3).
- **Other members:** their activity is positive (z≈2). It mostly tracks popularity, though, since everyone in Congress trades the same megacaps. It adds nothing out of sample.
- **Analysts:** upgrades in the prior 30 days are slightly *negative* (z≈−2.3). She is not following the analysts.

**Her picks against simple stand-ins** (`pick_vs_proxies_summary.csv`):
- Over 12 months her pick beat the "10 biggest stocks" basket by +8.0% on average.
- Bootstrapped by ticker, the 90% range is −1.8% to +18.0%, so the edge is not statistically reliable.
- Nvidia is +40% of the gap; MSFT (−26%) and CRM (−32%) went the other way.

## 4. Copying her: what you could actually earn (`strategy_*.csv`)

Rules: buy the stock (not the options) the day after each disclosure and hold it 12 months. Equal weight across open names, idle cash earns T-bills, and each trade costs 10bp. Period: 2014-06 to 2026-10.

| Strategy | CAGR | Volatility | Max drawdown |
|---|---|---|---|
| Copy on disclosure, 12m | **26.1%** | 27.7% | −47.7% |
| Copy, excluding NVDA | 19.3% | 26.8% | −46.3% |
| Her own timing (not copyable) | 27.1% | 28.2% | −49.4% |
| Hold every name she has ever disclosed | 16.2% | 25.4% | −47.9% |
| QQQ | 19.5% | 21.5% | −35.1% |
| SPY | 13.9% | 17.3% | −33.7% |
| NANC (since 2023-02) | 22.9% | 16.7% | −20.9% |

- **The reporting delay costs little.** Waiting about 25 days for the disclosure loses about 1%/yr against her own timing. Day by day, entering at disclosure instead of on her trade date costs 2–4 points of excess return per trade, at 20 days and at 12 months alike.
- **The result is mostly one stock in one year.** 2024 returned +156% because of NVDA; without NVDA it was +39%.
- **Bad years are bad:** 2022 was −42% (QQQ −33%), 2018 −16%, 2014 −9% and 2015 −7%.
- **Where copying beats QQQ:** in the concentration on a few winners, which is not a repeatable timing signal.

## 5. What this can't tell you

- **Committee work and bills.** Committee assignments, bill dates (CHIPS Act, antitrust) and hearings aren't in this data. These are the remaining real "information" questions, and they are the natural next pull (congress.gov).
- **News.** News around each trade date isn't tested.
- **Sample size.** It is small: 53–79 buys, highly concentrated in a few names, with a heavy tech tailwind from 2016 to 2025.
- **Survivorship bias.** The universe comes from tickers members traded and that Yahoo still prices; 1,626 dead tickers are missing (see NOTES).
- **The law.** 5 U.S.C. 13107(c) bars using these reports for commercial purposes other than news. Trading real money on them is a decision for a person; this is research.
