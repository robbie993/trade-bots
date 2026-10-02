# Atlas R2: results (run 2026-10-02, exactly as pre-registered)

Spec: `atlas_research/PREREG_R2.md` (committed before any R2 backtest).
Raw numbers: `r2_report.json`. Run: `python -m atlas_research.r2.run_r2`.
7,257 genomes evaluated across 3 families x 6 windows, 268 seconds.

## Verdict: all three families are research failures

On the five clean test years (2015, 2017, 2019, 2021, 2023), stitched:

| family | GA champion CAGR | max DD | Sharpe | years beating SPY | hand default, no search | verdict |
|---|---|---|---|---|---|---|
| SPY | +21.7% | -11.9% | 1.51 | | | |
| R2-A SPY core + momentum sleeve | +15.1% | -15.5% | 1.08 | 1/5 | +15.5%, -13.1% | research failure |
| R2-B ETF momentum rotation | +6.3% | -13.5% | 0.46 | 0/5 | +0.9%, -19.6% | research failure |
| R2-D vol-sized SPY + overlay | +19.1% | -15.3% | 1.27 | 2/5 | +19.9%, -14.6% | research failure |
| R2-C stocks + quality/value | not run: no point-in-time fundamentals or delisted-stock history | | | | | |

Per year (GA champion / SPY):

| test | R2-A | R2-B | R2-D | SPY |
|---|---|---|---|---|
| 2015 | -10.7% | -7.3% | -5.3% | +1.3% |
| 2017 | +23.3% | +13.8% | +24.3% | +21.0% |
| 2019 | +28.0% | +5.0% | +29.0% | +31.2% |
| 2021 | +28.5% | +19.1% | +32.2% | +30.6% |
| 2023 | +11.6% | +2.8% | +19.3% | +27.1% |
| 2025 (seen, not scored) | +41.5% | +38.2% | +28.3% | +18.2% |

Across all six models (3 GA champions + 3 defaults) on the clean test days,
White's Reality Check p = 0.97 and Hansen SPA p = 1.00: nothing beat SPY.
Doubled costs and tripled slippage moved CAGRs by 1 point at most, so costs
are not the reason.

## What it says

1. **The GA added nothing.** In A and D the hand-written default did as well
   as or better than the searched champion. In B the search rescued a badly
   losing default but still made 6% a year in a 22% market.
2. **Rotation is the weakest idea here, not the strongest.** Leaving SPY for
   whatever led over the last few months lost to SPY in every clean year.
   2025 was the exception (gold and silver), and it is the year we had
   already seen.
3. **Staying close to SPY gets close to SPY, and no further.** R2-D (SPY
   sized by vol, up to 120%) came closest. It beat SPY in 2017 and 2021, but
   lost 2015 and 2023 by more. Its stitched drawdown was still deeper than
   SPY's.
4. **These test years cannot test the defensive half.** By the pre-registered
   window design, the clean tests are the odd years 2015-2023. Those are
   almost all bull markets: SPY's worst drawdown across them is -11.9%. The
   bear years 2018, 2020 and 2022 sit only in validation. So R2 shows that
   no family out-earns SPY in good years. It says nothing about whether any
   of them would earn their keep in a crash.

## Honest state of the hunt

Two pre-registered rounds, roughly 11,700 genomes, and no strategy in reach
of this data beats SPY on return out of sample. Every round that "wins"
does it in the one period that is already spent (2025-26, precious metals).
No untouched historical data is left. Forward paper trading is the only
clean test remaining for anything new.
