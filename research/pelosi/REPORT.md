# Project Pelosi: results

Run 2026-10-07. Every number here comes from `study.py` on the data in `data/`, or is marked as an estimate. Paper research only: nothing was traded and nothing was deployed.

## Executive summary

**Round 2 update (section 10), after Robbie's pushback:**
- **She does beat SPY, consistently.** The realistic copy beat SPY in 8 of 11 full years, 83% of 3-year windows and 92% of 5-year windows.
- **Holding each of her buys for 2 years** (and ignoring her sales) makes it 30.8% a year vs SPY 13.8% and QQQ 19.2%. It beat SPY in almost every 3-year window and QQQ in 81%.
- **Her edge is which stocks she owns, not when she buys.** Her exact dates did slightly worse than random days on the same stocks. Her copy only matched SPY while she was Speaker, and the big gains came after she left leadership.
- **A public rule gets the same thing without her:** the 10 most-traded US stocks, refreshed monthly. It made 29.6% a year (Sharpe 1.04) and already held 52% of her buys before she made them.
- **Every version loses badly in a tech bear year like 2022**, so all of them fail the plan's validation rule. That is a big-tech beta bet, not hidden information.

**Round 1 summary:**

- **Copying Pelosi made money, but not because of anything special she knows.** A realistic copier who bought the day after each filing went public and sold when she disclosed a sale made **23.6% a year** from Dec 2014 to Oct 2026. Over the same span SPY made 13.8%, QQQ 19.2% and the tech ETF XLK 22.6%.
- **It is a leveraged big-tech bet.** Its beta to QQQ is 1.2. After controlling for SPY, QQQ and momentum, the leftover alpha is +3.5% a year with t = 0.8, which is not significant. Its Sharpe of 0.83 is the same as QQQ's (0.83), and its worst drawdown was **-49.5%** against QQQ's -35%.
- **It rests on a few trades.** NVDA (Nov 2023 calls), CRWD (2020) and AVGO (2024) carry it. Without those three, CAGR falls to 17.4%, below QQQ. From 2014 to 2022 the copy did no better than SPY. All of its lead comes from 2023-2026, the AI boom.
- **Disclosure delay matters less than people think, because there is little timing edge to lose.** Buying on her actual trade date beats buying on the public date by about 3 points at 1 month and about 4 points at 1 year. Against an equal-weight mega-cap tech basket, every delay is roughly zero.
- **Copying her options is reckless.** Buying modeled calls with her dollar weights loses heavily (max drawdown about -92%), driven mostly by the 2021 calls that expired in 2022.
- **Her sales are not sell signals.** Stocks she sold went on to beat SPY by **+16.9%** over the next year (95% CI +8 to +32). Half of her sales and gifts happen in December (taxes and charity).
- **The "beat Pelosi" bot failed walk-forward.** The best filter on 2014-2020 ("calls only, hold 1 year", +19.9% vs SPY) lost -12.3% in 2021-2022 validation, then made +29.9% in 2023-2026 (n = 9). That inconsistency is a kill.
- **Congress-wide, the average member loses to SPY.** Across 18,702 House and Senate trades from 2023-2026, the average stock buy trailed SPY by **-1.9% at 3 months** and **-4.3% at 6 months**, and $250k+ buys trailed by -10.3% at 6 months. Picking the 10 members with the best past record gave +1.1% (CI -1.5 to +4.0), which is not enough to trade. **The full 2014-2026 rerun (122,522 trades, 378 members) agrees:** the average buy is about zero vs SPY in 2014-2022 and negative since 2023, and the call-option slice that looked good in 2023-26 lost in 2019-22 (-1.4% at 6 months, n = 189).
- **Verdict: the copy thesis fails the project's pass bar.** It beats SPY on return but not on drawdown, its edge disappears after controlling for big tech, it is fragile, and nothing filtered survives out-of-sample. Recommended bot: none for real money. Two small, evidence-backed changes to the village's congress scanner are proposed for paper scoring only.

## 1. Data and reconciliation (doc sections 3-8)

| Item | Result |
|---|---|
| Raw Pelosi rows (HF point-in-time dataset) | 216 |
| Economic events after merging amendments/duplicates | 207 (5 merged) |
| Kinds | 64 call buys, 18 stock buys, 46 stock sales, 10 call sales, 32 exercises, 14 gifts, 5 corporate actions, 18 private (LLCs, real estate) |
| House Clerk PTRs found / in dataset | 67 / 63. The 4 missing are 3 amendments of trades already counted plus 1 private LLC (checked against the PDFs) |
| Filing delay, transaction to public | median 24 days, 90th percentile 38, max 141 |
| First trade / last public filing | 2013-11-26 / 2026-08-21 |
| Unpriceable buys | SunEdison 2014, six Hertz call buys 2014-15, Slack 2020 (delisted or ticker reused) |

Fixes applied: the 2014 Hertz and Disney rows only said "Purchase of N Options", so strike and expiry were filled from amended PDF 20003320. Old SUNE, HTZ, AA and DOW price history belongs to different companies today and was blanked. FB was mapped to META and SQ to XYZ. Exercises are treated as the lifecycle of an earlier call buy, not as new signals. Gifts and spinoffs are not signals.

## 2. Delay ladder: excess return per buy vs SPY (doc sections 7 and 12)

Mean per buy, 95% CI from a bootstrap that resamples filings. n = 61 at 1 year. Cost 10 bps.

| Entry | 1 month | 3 months | 1 year | 1 yr vs QQQ | 1 yr vs tech basket |
|---|---|---|---|---|---|
| Trade date (diagnostic only) | +3.9% [+0.1, +8.6] | +2.0% | +13.9% [+1.4, +26.8] | +10.5% | +1.0% |
| Trade +7 days | -0.2% | +0.5% | +11.9% | +8.9% | -0.9% |
| Trade +30 days | +0.2% | +1.4% | +11.4% | +8.5% | -2.3% |
| Public same day (optimistic) | +2.7% | +2.6% | +11.8% | +9.5% | -0.4% |
| **Public next day (realistic)** | **+1.0% [-4.0, +7.0]** | **+1.1%** | **+10.1% [-1.6, +23.1]** | **+7.7%** | **-2.2%** |
| Public +1 month | +0.8% | +2.4% | +11.7% | +9.2% | -1.1% |

The "tech basket" is AAPL, MSFT, AMZN, GOOGL, META and NVDA, equal weight. It is picked with hindsight, so it is a tough comparison, but it is the honest answer to "is it skill or just big tech?"

After she sold, the stocks she sold beat SPY by +3.1% at 1 month, +4.9% at 3 months, +12.3% at 6 months and +16.9% at 1 year.

## 3. Copy portfolio (doc sections 12-14)

Each buy is weighted by its disclosed dollar midpoint and re-weighted at every event. A position closes when she discloses a sale of that ticker. Idle cash sits in BIL. Costs: 5 bps per stock leg and 1% per option leg.

| Strategy (Dec 2014 to Oct 2026) | CAGR | Sharpe | Max DD | Beta (SPY) | 3-factor alpha (t) |
|---|---|---|---|---|---|
| Copy at trade date (diagnostic) | 24.6% | 0.85 | -51.6% | 1.26 | +4.3% (1.0) |
| **Copy, realistic (public next day)** | **23.6%** | **0.83** | **-49.5%** | **1.2** | **+3.5% (0.8)** |
| Copy, public +1 month | 22.2% | 0.79 | -51.3% | 1.2 | +2.2% (0.5) |
| Options-aware copy, realistic (modeled) | 9.5% | 0.45 | -92.6% | 2.4 | -8.4% (-0.6) |
| SPY | 13.8% | 0.72 | -33.7% | 1.0 | |
| QQQ | 19.2% | 0.83 | -35.1% | | |
| XLK | 22.6% | 0.89 | -33.6% | | |
| SMH | 31.8% | 0.97 | -45.3% | | |
| Tech basket (hindsight) | 33.9% | 1.15 | -47.8% | | |

- Per position: 74 positions, 73% made money, 57% beat SPY over their own holding period, profit factor 7.1, median hold 401 trading days. Win rate looks great only because most positions were big tech held for years in a bull market.
- **NANC** (the real Democrat-trades ETF), Feb 2023 to Oct 2026: NANC 23.3% a year, SPY 20.5%, QQQ 29.0%, and our copy 51.1% (beta 1.5, driven by NVDA and AVGO).
- By year, the copy lost badly in 2018 (-14% vs SPY -5%) and 2022 (-43% vs SPY -18% and QQQ -33%), and won big in 2023-2024.

## 4. Filtered, ranked and timing strategies with walk-forward (doc sections 12 and 19)

Splits: train 2014-2020, validate 2021-2022, untouched test 2023-2026. The filter and hold were picked on train only and then locked.

| Filter (1-year hold, mean vs SPY) | Train | Validate | Test |
|---|---|---|---|
| All buys | +19.3% (27) | -9.7% (23) | +28.9% (11) |
| **Calls only (train winner)** | **+19.9% (23)** | **-12.3% (17)** | **+29.9% (9)** |
| Stock buys only | +15.6% (4) | -2.1% (6) | +24.3% (2) |
| Tech only | +18.3% (23) | -12.7% (17) | +34.7% (10) |
| Fresh filings (lag 20 days or less) | +29.9% (5) | -10.1% (11) | +15.9% (10) |
| Top half by dollars | +18.0% (16) | -6.6% (15) | +34.6% (8) |

Every filter has the same shape: up in bull markets, down in 2022. That is tech beta, not stock-picking. Holding periods were checked too: a fixed 63-day hold gave 14.6% CAGR and a fixed 252-day hold gave 29.3% CAGR with a -55% drawdown, so the longer the hold, the more beta it collects.

## 5. Stress tests (doc section 20)

| Test | CAGR | Sharpe | Max DD |
|---|---|---|---|
| Base (realistic) | 23.6% | 0.83 | -49.5% |
| 2x costs | 23.4% | 0.82 | -49.6% |
| 5 more trading days of delay | 23.9% | 0.83 | -49.7% |
| **Without the top 3 trades** | **17.4%** | **0.67** | -48.8% |
| Equal weight instead of dollar weight | 28.2% | 1.00 | -46.4% |
| 2014-2019 only | 12.9% (SPY 11.5, QQQ 16.4) | 0.61 | -38.8% |
| 2020-2022 only | 6.0% (SPY 7.1, QQQ 7.6) | 0.33 | -49.5% |
| 2023-2026 only | 56.8% (SPY 22.4, QQQ 33.1) | 1.53 | -29.7% |

- Survivorship: adding the 8 unpriceable buys at approximate public-record outcomes cuts the 1-year excess from +10.1% to +5.1%. These outcomes are estimates, not from the price file: Hertz calls expired worthless, SunEdison went bankrupt, Slack was bought out.
- Regime: buys made when SPY was above its 200-day average did no better than buys made below it (both +10% at 1 year).

## 6. Why she traded (doc section 17)

Method: every buy and sell decision (52 of them, grouped by date) was checked against news, company filings, bills, votes, hearings and government contracts from before the trade. Each decision got a best public explanation, a confidence level, and a flag for any legislative or regulatory link. The full table is `out/why_trades.csv`, and the scored buys are `out/why_buys_scored.csv`. Private intent can't be determined from public data, and nothing here claims it.

**What the record shows**
- **86% of her buys came right after a public catalyst**: an earnings report, a crash, a stock split, an IPO, or big news. Examples: Apple the day after Buffett revealed his stake (2016-05-17); Facebook the day after its record crash (2018-07-27); Roblox on its listing day; NVDA the day after earnings (2023-11-22); Palo Alto the day after its 28% drop (2024-02-21); Intel the day after its blowout quarter (2026-07-24).
- **Her calls are deep in-the-money LEAPs** (median stock-to-strike ratio about 1.7, roughly 1-year expiries). That is leveraged stock ownership, not a bet on a surprise.
- **Sales are mostly for taxes and estate reasons.** 50% of her sales and 57% of her gifts happen in December. Visa sales trim the stake from Visa's 2008 IPO.
- **12 of 42 buy decisions had a legislative or regulatory link.** Those linked buys did *worse*: **+0.4%** vs SPY over the next year, against **+20.9%** for buys with no link. The 5 where the link favored the trade (chip subsidies, the Pentagon cloud contract, EV credits, the Intel stake) averaged **-11%**. That is the opposite of what profiting from inside legislative knowledge would look like.
- By catalyst type, one year vs SPY: right after earnings or a split announcement **+43%** (n = 6); dip buys +13% (n = 13); momentum +12% (n = 7); IPO-day buys -46% (n = 2). The samples are tiny.

**The controversial trades, checked against what was public at the time**

| Trade | What was public before | What came after | Read |
|---|---|---|---|
| SunEdison buy, 2014-10-24 | Solar growth story, TerraForm IPO | First Wind deal 24 days later (+29% that day); bankrupt 2016 | Lucky timing then a total loss (low confidence either way) |
| Amazon calls, 2019-07-22 | House Judiciary antitrust probe of Big Tech; CEO hearing 07-16 | Probe ran through 2020 | Bought despite unfavorable legislative news |
| Microsoft calls, Feb 2020 | Bought into the COVID crash | Army HoloLens contract ($21.9B) 2021-03-31, 12 days after exercise | Award was expected after the 2018 prototype deal; low confidence of a link |
| Amazon calls, 2021-05-21 | Reports that the Pentagon was reconsidering JEDI | JEDI cancelled and Amazon brought into JWCC 07-06 | Partly public; medium |
| GOOGL exercise and Big Tech calls, June 2021 | Six antitrust bills headed to markup | Judiciary passed them 06-24; never got a floor vote under Speaker Pelosi | Information cut against the trade; stalling is plausible but unproven |
| NVDA calls 2021 and exercise 2022-06-17 | Chip subsidies (USICA passed the Senate 2021-06-08) were public | CHIPS Act passed 2022-07-27/28 | **She sold 25,000 NVDA at a $341k loss the day before the vote**, after public pressure |
| GOOGL sales, Dec 2022 | Bloomberg reported in Aug 2022 that DOJ was "poised to sue" over ad tech | DOJ sued 2023-01-24 | Public, plus year-end tax-loss selling |
| Visa sale, 2024-07-01 | Visa disclosed a DOJ debit probe in 2021 | DOJ sued 2024-09-24 | Public probe and a decade-long selling pattern |
| PayPal sale, 2025-12-30 | Year-end | Fell to about $40 in Feb 2026 on guidance and a CEO change | Not foreseeable from public information; luck |

Sources checked: Fox News on SunEdison and First Wind; Fortune 2014-11-20 (Hertz CEO); Fool/Investing.com 2015-07-17 (Hertz restatement); Reuters 2016-01-07 (Apple under $100); Fortune 2016-05-16 (Buffett's Apple stake); TechCrunch 2018-03-23 (Dropbox IPO); Fool and Bloomberg 2018-07-26 (Facebook's crash); Fool 2018-10-24 (AT&T); Fortune 2019-06-10 (Tableau); Axios 2019-07-17 (Netflix); Boing Boing 2019-07-09 (Big Tech antitrust hearing); Fox Business (Microsoft IVAS; CrowdStrike); Variety/CNBC 2020-12-11 (Disney Investor Day); Bloomberg 2021-03-10 (Roblox listing); Reclaim The Net (JEDI/JWCC); Sludge/The Brick House (2021 antitrust bills); Free Beacon 2022-07-20 and Benzinga 2022-07-27 (NVDA and CHIPS); Bloomberg Law Aug 2022 and Just the News 2023-01-25 (Google ad-tech suit); Legal Insurrection 2024-09 (Visa); InvestorPlace (Broadcom split); AOL/Fox 2025-01 (pre-inauguration trades); IBTimes (PayPal); Bloomberg/Nasdaq 2026-05 (Intel and Apple foundry); Disclosed Capitol 2026-08-28 (Intel Q2); Sentisense/TIKR 2026-07 (Bloom Energy selloff); House Clerk PTR PDFs for every trade.

**Do the why-features predict returns?** No. On price features among her buys (momentum, distance from high, earnings timing, VIX), every p-value was above 0.2. Across 8,475 congress buys, pre-trade momentum had zero predictive power (rho 0.005). The legislative link predicted *lower* returns. Per the kill rule, none are used as trading signals. Historical options-market activity (unusual call volume before her trades) could not be checked: there is no options data, so that stays to be determined.

## 7. Congress-wide (doc sections 12-13; 2023 to Jul 2026, 144 members, 893 tickers)

| Signal | 1 month | 3 months | 6 months | n |
|---|---|---|---|---|
| All stock buys vs SPY | -0.4% | **-1.9%** [-2.7, -1.2] | **-4.3%** [-5.3, -3.3] | 8,970 |
| All stock sales vs SPY | -0.5% | -1.5% | -2.6% | 9,652 |
| Big buys ($250k+) | -0.9% | -5.7% | -10.3% | 28 |
| Call-option buys (the scanner's current gap) | -0.3% | +2.3% | +5.2% [-0.4, +10.6] | 40 |
| Top 10 members by past record (walk-forward by quarter) | | +1.1% [-1.5, +4.0] | | 508 |
| Same quarters, all members | | -1.6% | | 6,388 |
| Bottom 10 members (control) | | -1.1% | | 347 |

Senate buys did -0.4% at 3 months and House buys -2.0%. This window uses the old Alpaca daily file (adjusted, Dec 2022 to Jul 2026), because the PC's full congress price download didn't finish. The full-history rerun below replaces "to be determined" for pre-2023.

### 7b. Congress-wide, full history 2014-2026 (`congress_full.py`)

Prices: the PC's yearly `congress_prices_YYYY.parquet` (Yahoo adjusted close, 3,400 tickers). Of 146,617 House and Senate stock and call-option trades made public since 2014, 122,522 could be priced. 24,095 (16%) were dropped: 1,625 tickers have no Yahoo history (mostly delisted or acquired companies), and a data-quality screen removed 177 more (foreign OTC lines and tickers with a one-day move over 3x, which were bad prints). So this is biased toward companies that survived. Same rules as above: buy at the close of the first session after the trade went public, 10 bps cost, return minus SPY. Brackets are 95% CIs, resampled by filing date.

Average stock buy vs SPY, by when it went public:

| Period | 1 month | 3 months | 6 months | 1 year | n |
|---|---|---|---|---|---|
| 2014-2018 | -0.1% | -0.1% | -0.1% [-0.6, +0.5] | 0.0% | 21,129 |
| 2019-2022 | +0.4% | +0.1% | -0.2% [-1.0, +0.7] | +0.9% | 23,020 |
| 2023-2026 | -0.3% | -1.2% | **-2.7%** [-3.6, -1.9] | -3.5% | 15,283 |
| All | 0.0% | -0.4% | **-0.8%** [-1.2, -0.3] | -0.4% | 59,432 |

The median buy trails SPY by 1.8% at 6 months, and only 45% of buys beat SPY.

Other slices, 6 months vs SPY:

| Signal | 2014-2018 | 2019-2022 | 2023-2026 | All |
|---|---|---|---|---|
| Call-option buys | -3.9% (n = 7) | **-1.4%** [-7.5, +5.6] (n = 189) | +8.2% [+1.7, +17.2] (n = 48) | +0.4% [-4.8, +6.3] (n = 244) |
| Big buys ($250k+) | -2.4% | +1.7% | -5.7% | -1.0% (n = 493) |
| Stock sales | -0.9% | 0.0% | -1.5% | -0.7% |
| Senate buys | +1.1% | -0.2% | -0.9% | +0.3% [-0.8, +1.4] |
| House buys | -0.2% | -0.2% | -2.9% | -0.9% |
| Filed within 15 days | +0.3% | +0.8% | -2.5% | -0.3% |

Member skill, walk-forward by quarter from 2015 (rank members on buys whose 6-month result was already known, follow the top 10 next quarter):

| Group | 6 months vs SPY | n |
|---|---|---|
| Top 10 by past record | +0.8% [-1.5, +3.1] | 1,211 |
| Bottom 10 by past record | +0.3% [-1.8, +3.0] | 1,103 |
| All members, same quarters | -0.8% | 56,271 |

A member's 2014-2019 record barely predicts their 2020-2026 record (rank correlation 0.22, p = 0.11, 55 members with 10+ buys in each half). Momentum before the trade doesn't help either (rank correlation 0.02). Pelosi's own stock buys did +5.1% at 6 months [-0.8, +11.8] (n = 55), better than almost everyone, but that is the big-tech tilt from sections 3-5.

What it means:
- **There is no congress-wide edge to copy.** Over twelve years the average member's buys roughly match SPY, and since 2023 they trail it.
- **The call-option slice doesn't hold up.** It was the one positive result in 2023-26, but over 2019-22, with four times as many trades, it lost. The 2023-26 number is most likely the AI rally in big tech (where most of these calls are), not skill.
- **Following the "best" members doesn't work.** The top 10 did about as well as the bottom 10, and past skill barely carries forward.

## 8. Doc section 22, filled in

| Was "to be determined" | Now |
|---|---|
| Reconstructed Pelosi CAGR | 23.6% realistic copy (24.6% at trade date), Dec 2014 to Oct 2026 |
| Economic-event count | 207 (82 buys, 56 sales, 32 exercises, 37 non-signals) |
| Best disclosure delay | Trade date is best but unavailable. Among delays a copier can actually get, the curve is flat; the realistic delay loses about 3-4 points a year vs trade date |
| Realistic copy return | +10.1% per buy vs SPY at 1 year (CI -1.6 to +23.1); -2.2% vs the tech basket |
| SPY-relative alpha | +3.5%/yr after SPY, QQQ and momentum factors, t = 0.8, not significant |
| Beats SPY after delays and costs? | On return yes; on drawdown no; on risk-adjusted terms it ties QQQ |
| Best filtered strategy | Best copy rule: hold each buy 2 years and ignore her sales (30.8% CAGR, Sharpe 0.96). Best public rule: the 10 most-traded stocks each month (29.6%, Sharpe 1.04). Both fail 2021-22 validation (section 10) |
| Best bot | None for real money (see below) |
| Why-model predictive value | None. 86% of buys followed a public catalyst; buys with a legislative link did worse (+0.4% vs +20.9%) |
| Walk-forward performance | Train +19.9%, validate -12.3%, test +29.9%: inconsistent |
| Congress-wide before 2023 | Done (section 7b): average buy about 0% vs SPY in 2014-2022, call-option buys -1.4% at 6 months in 2019-22, member ranking no better than the bottom 10 |
| Final verdict | **She beats SPY consistently, but it's mega-cap tech beta that a public rule reproduces.** Copy thesis: fail as an edge; as a beta bet it works except in tech bear years. Original verdict: **Fail.** The apparent Pelosi edge is mega-cap tech beta plus a few big AI-era winners |

## 9. Bot (doc sections 18 and 23)

Round 2 (section 10.9) supersedes this: no copy bot; the honest version is a "10 most-traded stocks" paper firm, if wanted. Round 1 text: nothing passed, so there is no Pelosi bot to paper-trade. If you want Pelosi exposure, QQQ or XLK gave the same risk-adjusted return with a much smaller drawdown.

What the evidence does support, for the village's existing congress scanner (paper scoring in the idea lab only):
1. ~~**Score congress call-option buys**~~ as bullish calls on the underlying. They looked like the only positive slice in 2023-26 (+5.2% at 6 months, n = 40), but the full-history rerun (section 7b) shows them losing in 2019-22 (-1.4%, n = 189). The evidence no longer supports adding them; PR #38 should be closed rather than merged.
2. **Do not weight members by past record.** The walk-forward lift was small and not significant, and the bottom-10 control did about as well as average.

Reproduce: `python research/pelosi/study.py <old Alpaca prices.json>`, then `congress_full.py`, `why_table.py`, `round2.py`, `round2b.py` and `precursors.py` (data from branch `research/pelosi-data`). Tables are in `research/pelosi/out/`.

## 10. Round 2: where she does beat SPY (`round2.py`, `round2b.py`)

Robbie's pushback was that she beats SPY pretty consistently. **She does.** This section starts from that and tests how far it goes.

### 10.1 How consistent is it?

Realistic copy (buy the day after each filing goes public, sell when she discloses a sale), by calendar year:

| Year | Copy | SPY | QQQ | QQQ at 1.2x (same risk as the copy) |
|---|---|---|---|---|
| 2015 | 11.7% | 1.2% | 9.4% | 11.0% |
| 2016 | 9.7% | 12.0% | 7.1% | 8.2% |
| 2017 | 30.9% | 21.7% | 32.7% | 40.0% |
| 2018 | -14.3% | -4.6% | -0.1% | -1.1% |
| 2019 | 33.9% | 31.2% | 39.0% | 47.3% |
| 2020 | 52.8% | 18.3% | 48.4% | 58.0% |
| 2021 | 37.8% | 28.7% | 27.4% | 33.2% |
| 2022 | -42.6% | -18.2% | -32.6% | -38.6% |
| 2023 | 77.7% | 26.2% | 54.9% | 66.7% |
| 2024 | 87.3% | 24.9% | 25.6% | 29.6% |
| 2025 | 31.0% | 17.7% | 20.8% | 23.6% |
| 2026 to Oct 5 | 25.0% | 14.5% | 23.5% | 27.7% |

- **She beat SPY in 8 of 11 full years**, in 83% of all rolling 3-year windows and in 92% of rolling 5-year windows. The median 5-year window is 78 points ahead of SPY. That is consistent.
- QQQ also beat SPY in 8 of 11 years and in 92% of 3-year windows. Against QQQ the copy wins only 44% of 3-year windows. Against QQQ levered to the same risk (1.2x), it wins 7 of 11 years by calendar year but loses most multi-year windows, mainly because of 2022.
- Her own timing (trade date, which a copier can't get) beat SPY in 9 of 11 years.

### 10.2 Copy rules that keep more of it

Her sales are followed by her stocks beating SPY by +17% (section 2), so selling when she sells throws money away. Testing other exit rules (Dec 2014 to Oct 2026, 5 bps costs):

| Rule | CAGR | Sharpe | Sortino | Max DD | 3-factor alpha (t) | Beats SPY, 3-yr windows | Beats QQQ, 3-yr windows |
|---|---|---|---|---|---|---|---|
| Sell when she sells (baseline) | 23.6% | 0.83 | 1.13 | -49.5% | +3.5% (0.8) | 83% | 44% |
| **Never sell (keep her picks)** | 24.1% | 0.87 | 1.19 | -49.1% | +3.3% (1.0) | **93%** | **85%** |
| Hold each buy 1 year | 26.3% | 0.85 | 1.11 | -55.1% | +6.4% (1.1) | 84% | 62% |
| **Hold each buy 2 years** | **30.8%** | **0.96** | **1.32** | -50.7% | **+8.1% (1.6)** | **99.7%** | **81%** |
| Never sell, equal weight | 25.2% | 0.95 | 1.24 | -45.7% | +5.2% (1.6) | 82% | 73% |
| Sell when she sells, equal weight | 28.2% | 1.00 | 1.32 | -46.4% | | | |
| Her options, copied (modeled) | 9.5% | 0.45 | | -92.6% | -8.4% | 48% | 30% |
| SPY | 13.8% | 0.72 | | -33.7% | | | |
| QQQ | 19.2% | 0.83 | | -35.1% | | 92% | |
| QQQ at 1.2x | 22.3% | 0.83 | | -41.4% | 0.0% | 97% | 99.7% |
| XLK | 22.6% | 0.89 | | -33.6% | +2.3% (1.5) | 100% | 96% |

**Holding each of her buys for 2 years and ignoring her sales is the best copy found: 30.8% a year, beating SPY in almost every 3-year window and QQQ in 81% of them.** Its alpha after SPY, QQQ and momentum is +8.1% a year, but t = 1.6, so it is still not statistically solid.

Walk-forward check (choose the rule on 2014-2020 only, then lock it):

| Rule | Train 2014-20 vs SPY | Validate 2021-22 vs SPY | Test 2023-26 vs SPY | Test vs QQQ |
|---|---|---|---|---|
| Never sell, equal weight (train winner) | +18.7% | **-16.2%** | +23.6% | +12.9% |
| Hold 2 years | +12.2% | -15.9% | +28.8% | +18.1% |
| Sell when she sells | +6.1% | -16.8% | +27.3% | +16.6% |
| Hold 6 months | +7.4% | -3.0% | +20.2% | +9.5% |

Every rule has the same shape: well ahead in 2014-20, about 16 points behind SPY in 2021-22, well ahead in 2023-26. The rule choice only moves how much it wins. By the plan's kill rule (must survive validation), all of them fail on 2021-22. But that loss is one bad year for tech (2022), not a rule falling apart. **If you can sit through a 2022-style year (-43% vs SPY -18%), the hold-2-years copy has beaten SPY over almost every 3-year stretch since 2014.**

### 10.3 Is it timing or stock choice?

For every buy, I compared her 1-year return vs SPY with the same stock bought on 60 random days 1-12 months before or after:

| | Her date | Same stock, random days | Timing edge |
|---|---|---|---|
| Copier (public date) | +10.2% | +18.7% | -8.5% (CI -18.7 to +2.1) |
| Her own trade date | +14.0% | +16.8% | -2.8% (CI -11.5 to +5.9) |

- **Her timing adds nothing.** Even on her own trade dates, the same stocks bought at random times did slightly better. By decision, her timing beat random days 22 times and lost 29 times.
- **The edge is which stocks she owns:** mega-cap tech and semis, held for years. Against an equal-weight AAPL/MSFT/AMZN/GOOGL/META/NVDA basket over the same year, her picks did -2.1% (CI -9.1 to +6.6), and beat it only 39% of the time.
- By sector (1-year vs SPY per buy): payments +56% (4 buys), semis +21% (9), big tech +9% (24), media/consumer +6% (7), software/cyber -9% (11). Calls +10.6% (49) vs stock +8.2% (12).

### 10.4 Does power explain it?

| Era | Copy | Her trade date | Never sell | SPY | QQQ |
|---|---|---|---|---|---|
| Minority Leader, Dec 2014-2018 | 8.7% | 10.7% | 13.9% | 7.1% | 11.5% |
| **Speaker, 2019-2022** | 13.5% | 13.7% | 14.6% | **13.6%** | 16.0% |
| Out of leadership, 2023-2026 | 56.3% | 57.8% | 49.3% | 22.1% | 32.9% |

**Her best years came after she gave up the Speaker's gavel.** As Speaker, the most powerful seat in the House, her copy matched SPY and lost to QQQ. If her edge came from inside information, you'd expect the opposite. The 2023-26 run lines up with the AI rally in the stocks she already liked (NVDA, AVGO, big tech).

### 10.5 Published numbers vs ours (doc section 15)

| Source | Their number | Ours |
|---|---|---|
| Quiver "Nancy Pelosi" strategy (backtest, since May 2014) | 21.5%/yr, max DD -37.3%, Sharpe 0.75, beta 1.14 | 23.6%/yr, max DD -49.5%, Sharpe 0.83, beta 1.2 (copy); 73% of positions profitable, same as Quiver's 73% win rate |
| Unusual Whales 2022 | about -20% (S&P -19%) | Copy -42.6%; ours is more concentrated in her trades only |
| Unusual Whales 2023 | +65% (S&P +24%) | +77.7% |
| Unusual Whales 2024 | +70.9% (S&P +25%) | +87.3% |
| 2025 (24/7 Wall St) | +20.1% (S&P +16.6%) | +31.0% |
| Autopilot Pelosi Tracker (live) | +54% in 2024 | +87.3% (ours is a pure copy, theirs rebalances to a live account) |
| NANC ETF (all Democrats) | +88.5% from Feb 2023 launch | Copy 51.1%/yr vs NANC 23.3%/yr since launch |

Unusual Whales doesn't say how it weights options or whether it uses trade date; its wording points to trade date, which flatters the numbers. The direction matches ours every year.

Academic work disagrees on whether leaders have an edge:
- Wei & Zhou (NBER, Nov 2025, 20 leaders, 1995-2021) find leaders beat matched peers by up to 47 points a year after taking leadership, with sales ahead of hearings and regulatory actions.
- Chen & Sacerdote (NBER, Apr 2026, 2012-2023) find leaders trailed the market by 4.4 points at one year, and that trades in industries their own committees oversee did worse.
- Eggers & Hainmueller (2004-08) found Congress overall trailed the market. Belmont et al. (2012-2020) found no outperformance.

Our Pelosi-specific result fits Chen & Sacerdote: no outperformance while she held the most power, and trades with a legislative link did worse (section 6).

### 10.6 The trade-by-trade check, item by item (doc sections 16 and 17)

- Every historical example in doc section 16 was found in the ledger with matching contracts, strikes and dates: HTZ 50 calls at $22 expiring 1/15/2016, the 2021 AMZN, AAPL and NVDA calls, the GOOGL exercise, the June 2022 NVDA exercise at $100 (public 27 days later), the 2024 NVDA buys, the Dec 2024 exercise and sales, and the Jan 2025 calls. One correction: the TEM and VST trades on 2025-01-14 were calls, not stock.
- `out/why_trades.csv` now has a counter-evidence column for all 52 decisions. For buys it compares her date with random days on the same stock; for sales it shows whether the stock kept beating SPY afterwards. Result: her buy timing helped 22 times and hurt 29 times. Her sales avoided a lag 5 times, and 9 times the stock kept beating SPY after she sold.
- Options: of 58 modeled call positions, 12 lost more than half and 5 went to zero. Almost all of those were bought in Dec 2021 near the top (DIS, CRM, RBLX, MU and GOOG calls). The filings confirm these losses: for example, "expired with no value for a total loss of $303,001" on the RBLX calls. Copying her options would have doubled her 2022 drawdown.

### 10.7 Picking her stocks before she does (`precursors.py`)

Public rules only. At each month-end a rule picks 10 stocks from the 30 most-traded stocks (by 63-day dollar volume, among everything any member traded, ETFs and second share classes removed), holds them equal weight for a month, and pays 10 bps per trade. "Already held" means her later buy was in the rule's holdings at the month-end before her trade date.

| Rule | 2014-20 | 2021-22 | 2023-26 | All: CAGR / Sharpe / Max DD | Her buys already held |
|---|---|---|---|---|---|
| **10 most-traded stocks** | 31.4% | -15.2% | 59.5% | **29.6% / 1.04 / -52.2%** | **52%** |
| 30 most-traded, best 12-month momentum | 29.6% | -6.4% | 57.1% | 30.4% / 0.95 / -37.7% | 37% |
| 30 most-traded, best 6-month momentum | 31.7% | -10.8% | 44.4% | 27.0% / 0.89 / -41.1% | 35% |
| Dip in an uptrend (her own playbook) | 24.2% | -15.2% | 48.5% | 23.3% / 0.82 / -48.4% | 27% |
| Most bought by other members, last 60 days | 17.2% | -7.0% | 28.5% | 16.0% / 0.80 / -36.0% | 35% |
| Pelosi copy, realistic (for comparison) | | | | 23.6% / 0.83 / -49.5% | |
| Pelosi copy, hold 2 years | | | | 30.8% / 0.96 / -50.7% | |
| SPY | 12.6% | 3.4% | 22.3% | 13.7% / 0.82 / -33.7% | |
| QQQ | 20.9% | -6.7% | 33.0% | 19.0% / 0.91 / -35.1% | |

- **You can get her returns without her.** Just holding the 10 most-traded US stocks, chosen fresh each month from public volume data, made 29.6% a year with a Sharpe of 1.04. That's better risk-adjusted than any version of the Pelosi copy, and it doesn't wait 25 days for a filing.
- **It already owned half her picks before she bought them.** 52% of her buys were in its holdings the month before her trade date. 68% were among the 30 most-traded stocks. 75% had been bought by other members of Congress (median 6 buys) in the 90 days before her.
- **It has the same weakness.** Picked on 2014-20 (best Sharpe), it lost 15% a year in 2021-22 while SPY made 3%, then made 60% a year in 2023-26. It's the same mega-cap tech bet she makes, so it fails the plan's validation rule the same way.
- Survivorship: the universe only includes stocks that still have Yahoo prices today. That matters little for the 10 most-traded names, which are giant companies, but it is not zero.

### 10.8 How she gets her picks, as far as public data can show

- **The stocks:** her buys are mostly names that are already the most traded in the market (section 10.7). Picking them takes no inside knowledge.
- **The timing:** 86% of her buys came right after public news, mostly earnings, dips, splits and IPOs (section 6). Her exact dates did no better than random days on the same stocks (section 10.3).
- **Her power:** her copy only matched SPY during her four years as Speaker, the most powerful years she had. Trades linked to bills or regulators did worse (+0.4% vs +20.9%).
- **Her edge:** a long-run, concentrated, leveraged bet on Bay Area mega-cap tech, held through the 2023-26 AI boom. Paul Pelosi is a San Francisco venture and real-estate investor, so these are the companies he has watched for decades.
- **The way she buys options:** every call was in the money (median strike about 70% of the stock price), with about 12 months to expiry. That is a cheaper way to own the stock for a year. Someone trading on advance news would buy short-dated, out-of-the-money calls.
- **When the gains come:** her stocks do about +1% vs SPY in the 20 days after she buys and about +14% at 12 months. That is a slow build, not a jump after news.
- Source for the two points above: the PC's precursor study on branch `research/pelosi-data`, commit 1dab0a4, files in `research/pelosi/precursors/`. It ranked 500 large stocks at each buy and found that trading volume alone put her pick at a median rank of 13. Adding momentum, other members' trades, analyst upgrades or earnings dates did not do better.
- Non-public information can't be tested with public data, and this project doesn't try to obtain it. Nothing in the public record shows that she used it.

### 10.9 Updated verdict

- **She does beat SPY, consistently:** 8 of 11 years and 83% of 3-year windows. Holding her buys 2 years makes it 30.8% a year.
- **It isn't a secret.** A public rule (the 10 most-traded stocks) matches it with a better Sharpe and no filing delay. Both lose badly in a tech bear market like 2022.
- **For the village:** a Pelosi copy bot still isn't worth building. If you want this kind of return, the honest version is a "10 most-traded stocks, monthly" paper firm, scored against SPY and QQQ like every other firm. It is a big-tech beta bet with 2022-style drawdowns (-52%), not an edge, and it should be labeled that way.

### 10.10 Metrics the plan lists (doc section 14), realistic copy

Total return +1,112% (Dec 2014 to Oct 2026). CAGR 23.6%, volatility 28.1%, Sharpe 0.83, Sortino 1.13, Calmar 0.48, max drawdown -49.5%. Beta 1.23 to SPY. Exposure 99% (almost always invested). Turnover 3.7x a year. 74 positions: 73% made money, 57% beat SPY over their holding period, profit factor 7.1, average hold 546 trading days (median 401). Performance by delay: section 2. By trade type and sector: section 10.3. By regime: sections 5 and 10.4. Out of sample: sections 4 and 10.2.

### 10.11 Paper-trading rules, if Robbie wants the "10 most-traded" firm (doc sections 18 and 24)

1. On the last trading day of each month, rank US stocks by 63-day average dollar volume. Leave out ETFs and funds, and keep one share class per company.
2. Hold the top 10 at equal weight. Rebalance monthly.
3. Paper only, scored in the idea lab against SPY and QQQ after costs.
4. Kill it if it trails SPY by more than 15 points over any rolling 12 months, or trails QQQ over 3 years.
5. Expect 2022-style drawdowns (-52% in backtest). Present it as a beta bet, not an edge.

### 10.12 Limitations (doc section 21)

- Disclosure amounts are ranges, so weights use midpoints.
- Option results are modeled (Black-Scholes, no historical quotes), and the filings confirm the big losses.
- 16% of congress-wide trades had no price. The data is survivor-biased, and a quality screen removed 177 tickers with bad prints.
- The congress price file is from Yahoo; the Pelosi one also covers delisted names she traded via fixes in section 1.
- Leaders' non-public information can't be tested. No claim is made either way beyond what public data shows.
- Every result depends heavily on 2023-26, the AI rally.

## 11. Round 3: testing every public route to an edge (`round3.py`)

Robbie asked to try everything. Each test below looks for the fingerprint an information edge would leave in public data. Getting or using non-public information is illegal and is not attempted.

### 11.1 Did she know earnings in advance?

Next earnings report within 90 days after her trade, for the same tickers (Yahoo EPS history, 2013-2026):

| | n | Beat estimates | Same stocks' normal beat rate | Stock move on the report vs SPY | Same stocks' normal move |
|---|---|---|---|---|---|
| After her buys (median 55 days before the report) | 66 | 85% | 80% | **-0.8%** | +0.7% |
| After her sales (median 36 days before) | 47 | 79% | 79% | **+2.3%** | +0.7% |

No. The stocks she bought reacted worse than normal to their next report, and the stocks she sold reacted better. An insider would show the opposite on both.

### 11.2 Leak fingerprint: do her stocks jump right after she trades?

Return vs SPY from the close before her trade day, minus the same stock on 80 random days:

| | 1 day | 5 days | 20 days | n |
|---|---|---|---|---|
| Buys | -1.9% [-3.9, -0.3] | -0.2% | -1.6% | 73 |
| Sales (sign flipped, so + would mean she sold before a drop) | -0.3% | -0.8% | -2.7% | 49 |

- No jump. Her buys underperform random days in the first day (she buys on down days) and are flat after a month.
- In the 20 days before a buy, her stocks had trailed SPY by 2.3% (random days: +3.5%). She buys dips.
- Volume on her buy days is 1.3x normal (35% of days over 1.5x), which fits buying on news days that are already public. Her sale days have below-normal volume.

### 11.3 Congress-wide: whose account, and leaders

Stock buys, 6 months vs SPY (2014-2026):

| Group | Copy at public date | At their own trade date | n |
|---|---|---|---|
| Spouse's account | -0.8% | -0.9% | 24,005 |
| Member's own account | +0.2% | | 661 |
| Joint | -0.1% | -0.1% | 9,027 |
| Child's account | -1.3% | -1.6% | 10,974 |
| Leaders who trade (Pelosi, Clark, Jeffries, Boehner) | -2.4% | +0.6% | 340 |
| Everyone else | -0.8% | -0.8% | 59,092 |
| Pelosi alone | +5.1% | +6.2% [+0.2, +13.3] | 55 |
| Katherine Clark (Whip) | -3.6% | -0.2% | 270 |

- Spouse accounts like the Pelosis' do no better than anyone else's.
- The only leader with a positive record is Pelosi, and she's the big-tech story from section 10. Clark's leader-era trades lose to SPY.
- Leaders' trades do 3 points better at their own trade date than at the public date (+0.6% vs -2.4%), while everyone else's don't change. That's a small hint that leaders' timing is worth something before it's public, but the confidence interval includes zero and it's mostly one member's 270 trades.

### 11.4 Committee power and jurisdiction, all members (`committees_test.py`)

Committee seats for every member from 2013 to 2026 (36,394 rows: Stewart committee data plus current rosters, gathered on the PC). There are 58,000+ stock buys by 283 members with seat data. The tables give 6-month return vs SPY, at the member's own trade date.

| At the time of the buy, the member was... | Yes | No |
|---|---|---|
| A committee chair | -0.5% (13,285) | -0.8% |
| A ranking member | -1.8% (8,309) | -0.6% |
| A party leader | +2.0% [-2.2, +6.4] (101) | -0.8% |
| On a power committee (Ways and Means, Energy and Commerce, Financial Services, Appropriations, Armed Services, Intelligence, Senate Finance, Banking, Commerce) | -0.9% | -0.4% |
| **Buying a stock in an industry their committee oversees** | **+2.2% [+1.0, +3.5]** (2,580) | -0.9% |

The last row looked like a lead, so I dug in:
- By industry, the positive result is almost all tech: +12.1% for own-committee buyers vs +6.1% for other members. For defense, energy and transport, members buying under their own committee did *worse* (defense -3.2% vs +2.1%).
- **Comparing the same stock in the same quarter, own-committee buyers did +0.03% better than other members (t = 0.1, 1,047 matched cells).** The apparent edge came from which tech stocks they held (more NVDA, AMZN and META) and when, not from knowing something other members didn't.
- The busiest own-committee tech buyers were Michael McCaul (Foreign Affairs), Sheldon Whitehouse, Kurt Schrader, Don Beyer and Shelley Moore Capito.

Pelosi herself held no committee seats as leader or Speaker, so this test mainly covers other members.

### 11.5 More public channels tested on the PC (`research/pelosi-data`, `research/pelosi/channels/`, commit 2788749)

Each one compared activity around her trades with that company's normal rate, and with 2,000 sets of random dates (same stocks, dates moved up to a year).

| Channel | Data | Result |
|---|---|---|
| Federal contracts | 5,549 awards of $10M+, 2014-2026, 48 companies (USAspending) | No change in contract counts before or after her buys |
| Company insiders (SEC Form 4) | 264,000 rows | Executives did not buy ahead of her. They sold more than usual (1.7x) in the 90 days before her buys, which fits her buying after run-ups |
| Option volume (OCC) | Last 2 years only; no free older data | No unusual customer call buying in the 5 days before her 15 recent buys. 20% of her buy days had 1.5x normal call volume, vs 24% on ordinary days |
| Public attention (Wikipedia page views; GDELT news was blocked) | Daily views | Attention spikes were 3x more common in the month *after* her buys, and they trace to her own disclosures (TEM and VST in Jan 2025). Excluding disclosure week, there's no effect. Her filings are the news |
| Copy without NVDA | | 19.3%/yr, the same as QQQ, with more volatility and a -46% worst drop. 2024 alone was +156% because of NVDA |

Still being gathered: lobbying filings, every law 2013-2026 and every hearing with witnesses.

## 12. The public "Pelosi-like" strategy, tested properly (`mt_grid.py`)

Robbie passed on GPT's review: kill the copy bot, and instead test the public most-traded-stocks strategy in 12 steps, keeping 2022 in validation. This section runs steps 1, 2, 5-12. Valuation (step 3) and earnings catalysts (step 4) need fundamentals data, which is being gathered on the PC.

**Grid, fixed before looking:** 240 variants.
- Portfolio size: 5, 10, 20 or 30 stocks.
- Signal: most traded (size), 12-1 momentum, 6-month momentum, low volatility, or a size+momentum blend. The non-size signals pick from the 50 most traded.
- Weights: equal or inverse-volatility.
- Rebalance: monthly or weekly.
- Bear-market filter: none, SPY above its 200-day average, or QQQ above its 200-day average. When the filter is off, the money sits in T-bills.

The universe is point in time, ETFs are removed, and trading costs are 10 bps.

**Selection rule:** best Sharpe on train (Dec 2014 to 2020), then validate on 2021-22, then test on 2023 to Oct 2026.

| | Train CAGR | Validate (2021-22) | Test (2023-26) | Whole period CAGR / Sharpe / Max DD |
|---|---|---|---|---|
| **Train pick: 5 most traded, equal, monthly, no filter** | 35.4% | **-18.7%** | 59.6% | 30.7% / 1.02 / -51.5% |
| Average of the 10 best on train | | -15.8% | 51.2% | |
| 10 most traded, plain | 31.3% | -15.3% | 58.7% | 29.4% / 1.03 / -51.3% |
| **10 most traded + QQQ 200-day filter** | 22.5% | **+9.4%** | 34.5% | **23.8% / 1.02 / -31.1%** |
| 10 most traded + SPY 200-day filter | 21.2% | -11.5% | 30.4% | 17.7% / 0.81 / -46.9% |
| SPY | 12.6% | 3.4% | 22.3% | 13.7% / 0.82 / -33.7% |
| QQQ | 20.9% | -6.7% | 33.0% | 19.0% / 0.91 / -35.1% |

**The pre-registered pick failed validation again (-18.7% in 2021-22).** Every concentrated big-stock variant without a bear filter crashes in 2022.

**What the grid shows across all 240 variants:**
- **The QQQ 200-day filter is the one thing that fixes 2022.** Averaged over every variant, 2021-22 return goes from -8.3% (no filter) to -0.2% (QQQ filter). The SPY filter barely helps (-6.6%), because QQQ broke trend earlier in the 2022 tech bear.
- With the QQQ filter, 15% of variants beat SPY in all three periods, against 2% without a filter.
- Every 5- or 10-stock "most traded" variant with the QQQ filter beat SPY in 2021-22 (+5% to +13%). So it's a stable neighborhood, not one lucky setting:
  - 10 most traded, inverse-vol, monthly, QQQ filter: 22.4%/yr, Sharpe 1.06, max DD -29.5%.
  - 5 most traded, inverse-vol, monthly, QQQ filter: 23.6%/yr, Sharpe 1.06, max DD -26.9%.
  - Over the whole period, these beat SPY (13.7%) and QQQ (19.0%) with a smaller worst drop than either.
- Momentum signals did worst in 2022 (-9% to -12% on average). Low volatility survived 2022 but only made about 8% a year overall. Fewer stocks gave more return and bigger drawdowns. Inverse-vol weights traded a little return for smaller drawdowns. Weekly vs monthly rebalancing made little difference.

**Honest caveat:** the QQQ-filter version was not the train pick. I can see it works in 2021-22 only because I looked at 2021-22. 2022 is one bear market, and trend filters do well in slow bears and can get whipsawed in fast V-shaped crashes. It still held up in 2020's fast crash (train period) and in 2023-26. But there is no untouched history left. **The only real out-of-sample test is a forward paper book**, run like Atlas R3: rules frozen now, a shadow ledger, no orders.

**Proposed frozen rules ("Big-10 trend"):** on the last trading day of each month:
1. If QQQ closes above its 200-day average, hold the 10 most-traded US stocks (63-day average dollar volume, no ETFs, one share class), weighted by inverse 63-day volatility.
2. Otherwise hold T-bills.

Score it vs SPY and QQQ using the Atlas R3 pass bar: CAGR above SPY, max drawdown smaller than SPY's, Sharpe at least SPY's, and it survives 2x costs.

These rules already pass that bar on history, but only in-sample:
- 22.4% CAGR vs SPY 13.7%, Sharpe 1.06 vs 0.82, max drawdown -29.5% vs -33.7%.
- At 2x costs: 21.9%, Sharpe 1.05. At 5x costs: 20.7%, Sharpe 1.00.
- By period: 2021-22 +12.5% (SPY +3.4%), 2023-26 +30.7% (SPY 22.3%, QQQ 33.0%).
- The filter kept it invested 83% of the time.

**Update 2026-10-07 22:31 UTC:** Robbie chose to run this as a paper firm in the daily village on his PC. Because the village caps a firm at 8 positions and 25% per name, it holds **the top 5 at 20% each** (equal weight, monthly, QQQ 200-day filter). That version: 26.0%/yr, Sharpe 1.03, max DD -30.2%, 2021-22 +7.1%. Bot `bots/big5_trend.py`, firm `firm_big5_trend` in `config/firm_config_daily.yaml`, draft PR #39 (not merged; the PC runs it from that branch).

## 13. Hearings and laws vs her buys (`laws_hearings.py`)

Data: 15,537 congressional hearings (391 with company witnesses) and 2,165 laws (113th-119th Congress), pulled on the PC. Only one law names a company directly, so laws are matched by sector (239 flagged tech).

| Check | Her buys | Same stock, random days | Verdict |
|---|---|---|---|
| Company testified within 90 days of her trade | 22.0% | 19.6% | Same as chance |
| Buys where a hearing is likelier than at random | 18 of 82 | | Same as chance |
| Tech laws signed in the 90 days after a tech buy | 4.42 | 4.54 | Same as chance |

- **1-year return vs SPY:** buys near a company hearing **+4.6%** (17 buys) vs her other buys +12.4% (44). Tech buys followed by a tech law +10.4% (44) vs +17.6% without (6, too few to read).
- **Verdict:** her trades don't cluster around hearings or laws, and the ones that sit near them did worse, not better. This matches the committee test (section 11): no sign of trading on what Congress was about to do.

## 14. Valuation and earnings filters on the most-traded rule (`fund_filters.py`, GPT steps 3-4)

Pre-registered grid of 56 variants:
- Pool: the 20 or 30 most traded. Hold 5 or 10, equal weight, monthly. Filter: none or QQQ 200-day.
- Signals inside the pool: size (baseline), profitable only (positive trailing EPS), value (lowest trailing P/E), growth (trailing EPS growth vs a year ago), surprise (average EPS surprise over the last 2 reports), beat (beat estimates last report), and GARP (value plus growth).
- Point in time: an earnings report counts from the session after it. EPS is yfinance's split-adjusted reported EPS, covering 98.5% of the pool's slots.

**Result: no earnings or valuation filter improves the plain most-traded rule.** Each signal vs plain size at the same settings (8 pairs each):

| Signal | Whole-period CAGR vs size | Pairs where it beats size (whole / test) |
|---|---|---|
| Profitable only | -0.6 pts | 4 of 8 / 6 of 8 |
| Beat last estimate | -1.7 pts | 0 of 8 / 0 of 8 |
| Growth | -7.0 pts | 0 of 8 / 0 of 8 |
| Surprise (earnings drift) | -8.5 pts | 0 of 8 / 0 of 8 |
| GARP | -13.6 pts | 0 of 8 / 0 of 8 |
| Value (cheap P/E) | -22.3 pts | 0 of 8 / 0 of 8 |

- **Cheap P/E is the worst idea here.** Among the biggest stocks, the cheap ones are the laggards (banks, oil, telecom); the expensive ones are the AI and cloud names that drove returns.
- Surprise-chasing crashed in 2022 (-43% without the filter).
- The train pick is again plain "5 most traded, no filter" (train Sharpe 1.27), which fails 2021-22 at -18.7%, as in section 12.
- **One maybe:** "profitable only, 5 stocks, QQQ filter" made 27.7%/yr, Sharpe 1.10, max DD -25.4%, 2021-22 +12.0%. That is slightly better than the live Big-5 rule (26.0 / 1.03 / -30.2 / +7.1). It mainly drops money-losing names, such as early Tesla and the 2021 meme stocks. But across the 8 settings it won only half the time, so it's noise-level. **Big-5 Trend stays frozen as is.** Changing it now would be exactly the after-the-fact tuning the forward test is meant to avoid.

**Survivorship caveat (applies to sections 12 and 14):** prices come from tickers some member traded and that Yahoo still prices. Former top names that were delisted or acquired (Twitter, Celgene, Activision, Allergan, Xilinx, Splunk) are missing, so backtest returns are likely overstated. The PC is looking for free delisted-company prices to rerun this. The forward test doesn't have this problem.

## 15. Survivorship fixed: dead stocks added back (`survivor_fix.py`)

The PC recovered daily prices for 191 delisted stocks from Alpaca, 2016 to their delisting, plus 34 renamed tickers from Yahoo (PC commits 4cc61f5 and 562ad3f, `research/pelosi/delisted/NOTES.md`). 20 dead companies had been in the top 100 by dollar volume at some point: Twitter, Celgene, Allergan, Activision, Time Warner, Yahoo and others. Duplicate old tickers whose successors already carry the full history (ANTM, UTX, DWDP, VRX) are left out.

**Result: the bias was tiny for this rule.** Same rules, old universe → fixed universe:

| Version | Whole period CAGR | 2016-26 | 2021-22 | 2023-26 | Sharpe | Max DD |
|---|---|---|---|---|---|---|
| 5 most traded + QQQ filter (live Big-5 rule) | 26.0 → 26.0 | 28.5 → 28.5 | 7.1 → 7.1 | 38.6 → 38.6 | 1.03 → 1.03 | -30.2 → -30.2 |
| 10 most traded + QQQ filter | 23.8 → 24.2 | 26.1 → 26.5 | 9.4 → 9.4 | 34.5 → 34.5 | 1.02 → 1.03 | -31.1 → -31.1 |
| 10 most traded, inverse-vol + QQQ filter | 22.4 → 22.5 | 24.8 → 25.0 | 12.5 → 12.5 | 30.7 → 30.7 | 1.06 → 1.07 | -29.5 → -29.5 |
| SPY / QQQ (2016-26) | | 15.2 / 20.5 | | | | |

- **Why nothing moved:** the dead giants never ranked in the top 5. Twitter's best was 21st and Celgene's 28th. Only Allergan made the top 10, for 3 months, and it did slightly better than what it replaced. The 5 to 10 most-traded US stocks are the mega-caps (Apple, Microsoft, Nvidia, Amazon, Tesla, Meta), and none of those died.
- **What this does not fix:** stocks that died in 2014-15, since Alpaca starts in 2016. It also does not remove the hindsight in the QQQ filter (section 12). The forward paper test is still the real judge.

## 16. Lobbying, and every channel side by side (PC tests, research/pelosi-data fb7440f, `channels/NOTES.md`)

Lobbying data: 42,544 Senate lobbying filings, 2014-2026, for 107 of her companies; 37% name a specific bill. Each channel is tested against the stock's own base rate and against 2,000 placebo dates.

| Channel | Before her buys | After her buys | Around her sales |
|---|---|---|---|
| Lobbying (filings, dollars, bill-specific) | normal (0.8-0.9x) | normal | 1.3-1.6x, **both before and after** (p 0.01-0.08) |
| Federal contracts $10M+ | normal | normal | 2.9x before (7 stocks) |
| Insider open-market buys | normal | normal | normal |
| Insider sales | 1.7x (both follow run-ups) | 1.4x (not significant) | fewer |
| Unusual call buying (last 2 years only) | 20% vs 24% base | | none |
| Wikipedia attention | 1.4x (not significant) | 3.1x, **gone once her disclosure week is removed** | fewer |
| Hearings and laws (section 13) | same as random days | | |

The results around her sales are symmetric (before and after), weak against the local placebo, and come from about 40 tests. They reflect when her sales fell (big-tech antitrust years with heavy lobbying), not timing.

**Final verdict across all public routes:** nothing public moved ahead of her buys. Her results come from holding mega-cap tech, mostly Nvidia, plus some stock-picking. The one real, copyable piece is the public "most-traded stocks + QQQ trend" rule (sections 12, 14, 15), now running forward as the Big-5 Trend paper firm. A side effect worth knowing: small names jump when her filings become public (+5% vs SPY next day, Tempus +35%; megacaps +1.1%). That is a reaction to her disclosure, not an information edge.
