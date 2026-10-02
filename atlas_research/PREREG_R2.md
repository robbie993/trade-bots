# PRE-REGISTRATION: Atlas R2 (written 2026-10-02, before any R2 backtest ran)

Robbie's brief after R1 failed: stop making "defensive" the default, use SPY
as the hurdle at every stage, and test four separate architectures in the
same frozen walk-forward. The GA tunes each family internally and can never
switch families.

## Families

| | architecture | what the GA may tune |
|---|---|---|
| **R2-A** | permanent SPY core (60-80%) + momentum alpha sleeve | core share, sleeve size 1-3 ETFs, momentum weights, trend-permission length, what a sleeve slot holds when its asset fails permission (cash / SPY / IEF), gross 100-120% while SPY's trend is healthy |
| **R2-B** | cross-sectional ETF momentum rotation | top 1-5 ETFs, momentum weights, equal or inverse-vol, permission length, how much of a slot survives below its average (0 or 50%), the four-step exposure ladder |
| **R2-C** | stock momentum + quality/value | **not run**: needs point-in-time fundamentals and a stock history with delisted names. Neither is reachable from the cloud session. |
| **R2-D** | SPY with dynamic vol sizing + momentum overlay | SPY weight = target vol / SPY realised vol, clipped to [min 20-60%, max 100-120%]; 0-30% overlay in the top 1-2 ETFs with permission |

Universe: the same 14 ETFs as R1 (SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD
SLV DBC VNQ UUP). **The sector ETFs Robbie listed (XLK XLF XLE XLV XLI XLP)
have no price source after 2020-11 in reach of this session**, so they are
left out of every family instead of being covered for the test years with
stand-ins. They can be added once `data/pc_fetch.py` runs on the PC.

## Shared rules (fixed, not genes)

* **Trend permission** compares each asset to its own simple moving average.
  The length is a gene, chosen from 100, 150, 200 or 250 days only.
* **SPY trend state**, using that same average:
  * crash: SPY 20%+ below its 252-day high and 21-day vol above 30%
  * confirmed bear: SPY 3%+ below its average and 10%+ below its high
  * weakening: SPY below its average, or its 63-day return is negative
  * normal: everything else
* **Exposure ladder** (genes, Robbie's ranges): normal 100-120%, weakening
  70-100%, bear 30-70%, crash 0-40%. R2-A and R2-D only lever up in normal.
* **Leverage**: never above 120% gross. Borrowed money costs the T-bill rate
  plus 2.0% a year.
* **Timing**: decide at close t, trade at close t+1, same costs as R1. If the
  SPY state worsens between rebalances, the book is re-decided at once;
  improvements wait for the next scheduled rebalance.
* **Momentum score**: cross-sectional z of 21/63/126/252-day returns (optional
  5-day skip), weights bounded as in R1.

## Fitness (SPY is the hurdle everywhere)

Year by year, so that one crash cannot buy a pass:

    excess   = mean over calendar years of (Atlas year return - SPY year return),
               each year clipped to +/-15 points
    dd_gain  = mean over calendar years of (|SPY max DD in year| - |Atlas max DD in year|)
    capture  = upside capture - downside capture (monthly, vs SPY)

    score = 0.45 tanh(excess / 3%) + 0.25 tanh(dd_gain / 5%) + 0.15 tanh(capture / 0.3)
          + 0.075 tanh(dSharpe / 0.5) + 0.075 tanh(dCalmar / 0.3)

Hard gates while searching: average gross exposure of at least 50% and at
least 4 trades a year; each failure costs 2 points of fitness.

## Windows (Robbie's, rolling 8-year train)

| | train | validate | test |
|---|---|---|---|
| W1 | 2005-2012 | 2013-2014 | 2015 |
| W2 | 2007-2014 | 2015-2016 | 2017 |
| W3 | 2009-2016 | 2017-2018 | 2019 |
| W4 | 2011-2018 | 2019-2020 | 2021 |
| W5 | 2013-2020 | 2021-2022 | 2023 |
| W6 | 2015-2022 | 2023-2024 | 2025 |

**W6's test year is not clean.** 2025 (and 2026 to July) was spent as R1's
frozen OOS, and we both know silver and gold drove it. W6 is reported, but
kept out of the verdict. **No untouched historical period remains.** The only
clean out-of-sample test left for any R2 survivor is forward paper trading.

GA per window and family: 60 random genomes plus the family default, then 4
generations of top 10 x 10 descendants (6 aimed at autopsy failure tags).
The champion is the best of the top 10 on VALIDATE, and it is run once on TEST.

Each family's hand-written default genome is also run on every TEST year,
with no search, so we can see whether the GA adds anything.

## Verdict rules (decided now)

On the five clean test years (2015, 2017, 2019, 2021, 2023), stitched:

* **Candidate**: stitched CAGR > SPY **and** stitched max DD shallower than
  SPY's **and** the family beats SPY's return in at least 3 of the 5 years.
* **Strong candidate**: Candidate, and still beats SPY on both counts at 2x
  costs and at 3x slippage.
* **Forward paper candidate**: a strong candidate. A family can only go
  further than that live.
* Otherwise **interesting** (beats on one count) or **research failure**.

Across the families, White's Reality Check and Hansen's SPA are run on the
stitched clean test returns against SPY. The genome count is reported too.
