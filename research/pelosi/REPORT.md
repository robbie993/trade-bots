# Project Pelosi: results

Run 2026-10-07. Every number here comes from `study.py` on the data in `data/`, or is marked as an estimate. Paper research only: nothing was traded and nothing was deployed.

## Executive summary

- **Copying Pelosi made money, but not because of anything special she knows.** A realistic copier who bought the day after each filing went public and sold when she disclosed a sale made **23.6% a year** from Dec 2014 to Oct 2026. Over the same span SPY made 13.8%, QQQ 19.2% and the tech ETF XLK 22.6%.
- **It is a leveraged big-tech bet.** Its beta to QQQ is 1.2. After controlling for SPY, QQQ and momentum, the leftover alpha is +3.5% a year with t = 0.8, which is not significant. Its Sharpe of 0.83 is the same as QQQ's (0.83), and its worst drawdown was **-49.5%** against QQQ's -35%.
- **It rests on a few trades.** NVDA (Nov 2023 calls), CRWD (2020) and AVGO (2024) carry it. Without those three, CAGR falls to 17.4%, below QQQ. From 2014 to 2022 the copy did no better than SPY. All of its lead comes from 2023-2026, the AI boom.
- **Disclosure delay matters less than people think, because there is little timing edge to lose.** Buying on her actual trade date beats buying on the public date by about 3 points at 1 month and about 4 points at 1 year. Against an equal-weight mega-cap tech basket, every delay is roughly zero.
- **Copying her options is reckless.** Buying modeled calls with her dollar weights loses heavily (max drawdown about -92%), driven mostly by the 2021 calls that expired in 2022.
- **Her sales are not sell signals.** Stocks she sold went on to beat SPY by **+16.9%** over the next year (95% CI +8 to +32). Half of her sales and gifts happen in December (taxes and charity).
- **The "beat Pelosi" bot failed walk-forward.** The best filter on 2014-2020 ("calls only, hold 1 year", +19.9% vs SPY) lost -12.3% in 2021-2022 validation, then made +29.9% in 2023-2026 (n = 9). That inconsistency is a kill.
- **Congress-wide, the average member loses to SPY.** Across 18,702 House and Senate trades from 2023-2026, the average stock buy trailed SPY by **-1.9% at 3 months** and **-4.3% at 6 months**, and $250k+ buys trailed by -10.3% at 6 months. Picking the 10 members with the best past record gave +1.1% (CI -1.5 to +4.0), which is not enough to trade.
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

Private intent cannot be determined from public data. What the public record shows:

- **Her calls are deep in-the-money LEAPs, not lottery tickets.** Median stock-to-strike ratio is about 1.7, with roughly 1-year expiries. That is a leveraged way to hold the stock, not a bet on a surprise.
- **Mostly momentum in mega-cap tech.** Typical 12-month excess return before a buy was +50% to +200% (NVDA, MSFT, TSLA, VST, INTC).
- **December is for taxes and charity.** 50% of her sales and 57% of her gifts are in December.
- Notable trades and the best public explanation:

| Trade | Evidence available before it | Best explanation (confidence) | Counter-evidence |
|---|---|---|---|
| TSLA calls, 2020-12-22 | Tesla joined the S&P 500 on 12-21, up 54% in 3 months | Momentum and index-inclusion hype (high) | Lost 57% by the time she sold |
| MSFT/GOOGL calls, 2020-02-20 to 02-27 | Market peak, start of the COVID crash | Momentum (medium) | Bought at the top. Not consistent with foresight |
| CRWD stock, 2020-09-03 | 2 days after strong earnings | Post-earnings momentum (medium) | |
| NVDA calls, 2021-06-03 | 4-for-1 split announced 5-21, earnings 5-26 | Split and earnings momentum (high) | Lost 17% vs SPY over the next year |
| NVDA stock + calls 2021-07; sale 2022-07-26 | Chip subsidy bill (CHIPS) in Congress; she sold 25,000 shares the day before the vote | Legislative exposure is plausible but unproven; she sold at a loss under public pressure (medium) | The sale lost money, the opposite of insider gain |
| Dec 2021 calls on CRM, RBLX, MU, GOOG, DIS | Dip buys after Q4 drops (CRM -20% off its high) | Buying the dip in growth names (medium) | All lost 21-53% vs SPY in 2022 |
| NVDA calls, 2023-11-22 | The day after earnings | Post-earnings AI momentum (high) | Best trade in the dataset (+159% vs SPY) |
| PANW calls, 2024-02-12 and 02-21 | 02-21 was the day after the stock fell 28% on earnings | Momentum, then buying the dip (high) | |
| AVGO calls, 2024-06-24 | Earnings and a 10-for-1 split announced 6-12 | Split and earnings momentum (high) | |
| VST, TEM, AMZN, GOOGL, NVDA calls, 2025-01-14 | AI and power-for-data-centers theme, VST up 36% in 3 months | Theme momentum (medium) | VST lost 29% vs SPY |
| INTC, UBER, BE, 2026 | INTC up 141% in 3 months; BE down 44% in a month, 4 days before earnings | Momentum (INTC), buying the dip (BE) (low: recent, too early to score) | |

**Do the why-features predict returns?** No. Among her buys, none of 1-, 3- or 12-month momentum, distance from the 52-week high, chip-sector momentum, days to or since earnings, or VIX had a significant link to the next year's excess return (every p > 0.2, n of about 40). Across 8,475 congress buys, pre-trade momentum had zero predictive power (rho 0.005). Per the kill rule, they are not used as signals.

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

Senate buys did -0.4% at 3 months and House buys -2.0%. This window uses the old Alpaca daily file (adjusted, Dec 2022 to Jul 2026), because the PC's full congress price download didn't finish. Pre-2023 congress-wide is still to be determined.

## 8. Doc section 22, filled in

| Was "to be determined" | Now |
|---|---|
| Reconstructed Pelosi CAGR | 23.6% realistic copy (24.6% at trade date), Dec 2014 to Oct 2026 |
| Economic-event count | 207 (82 buys, 56 sales, 32 exercises, 37 non-signals) |
| Best disclosure delay | Trade date is best but unavailable. Among delays a copier can actually get, the curve is flat; the realistic delay loses about 3-4 points a year vs trade date |
| Realistic copy return | +10.1% per buy vs SPY at 1 year (CI -1.6 to +23.1); -2.2% vs the tech basket |
| SPY-relative alpha | +3.5%/yr after SPY, QQQ and momentum factors, t = 0.8, not significant |
| Beats SPY after delays and costs? | On return yes; on drawdown no; on risk-adjusted terms it ties QQQ |
| Best filtered strategy | None. The walk-forward pick failed validation |
| Best bot | None for real money (see below) |
| Why-model predictive value | None (all p > 0.2) |
| Walk-forward performance | Train +19.9%, validate -12.3%, test +29.9%: inconsistent |
| Final verdict | **Fail.** The apparent Pelosi edge is mega-cap tech beta plus a few big AI-era winners |

## 9. Bot (doc sections 18 and 23)

Nothing passed, so there is no Pelosi bot to paper-trade. If you want Pelosi exposure, QQQ or XLK gave the same risk-adjusted return with a much smaller drawdown.

What the evidence does support, for the village's existing congress scanner (paper scoring in the idea lab only):
1. **Score congress call-option buys** as bullish calls on the underlying. They are the only congress slice with a positive tilt (+5.2% at 6 months, n = 40, not yet significant), and the scanner currently skips them.
2. **Do not weight members by past record.** The walk-forward lift was small and not significant, and the bottom-10 control did about as well as average.

Reproduce: `python research/pelosi/study.py <old Alpaca prices.json>` (data from branch `research/pelosi-data`). Tables are in `research/pelosi/out/`.
