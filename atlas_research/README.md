# atlas_research: Atlas Phase 2 (SPY-R) research stack

Offline research only. Nothing in the live village imports this package, the
Dockerfile does not copy it, and running it never needs a deploy.

**Target:** higher annualised total return than SPY **and** a shallower
maximum drawdown than SPY, after realistic costs. Sharpe alone never passes
anything.

```
python -m pip install pandas numpy scipy pytest
python -m pytest atlas_research/tests -q             # look-ahead, leverage, idler tests
python -m atlas_research.run_core discover          # walk-forward + final search + gauntlet, freezes a champion
python -m atlas_research.run_core oos               # the sealed 2025-26 test, once
python -m atlas_research.report                     # results/REPORT.md
```

Data lives outside the repo (`ATLAS_DATA_DIR`, default
`/mnt/project-files/atlas_research`). Build it with `data/prices.py` (cloud:
Qlib's Yahoo dump + Alpaca bars) or `data/pc_fetch.py` (any machine that can
reach Yahoo and FRED).

## Layout

| module | what it does |
|---|---|
| `data/prices.py` | 14-ETF daily total-return panel, 2000 to 2026-07, every row labelled by source |
| `data/rates.py` | 3-month T-bill by year (approximate; 2025-26 estimated) |
| `data/fundamentals.py` | **not built**: refuses until point-in-time EDGAR data and a survivorship-free stock universe exist |
| `data/options.py` | **not used yet**: rules for keeping real and SYNTHETIC option data apart |
| `signals/momentum.py` | 21/63/126/252-day returns, optional 5-day skip, cross-sectional z |
| `signals/value.py` | asset-class value: 5-year reversal (Asness, Moskowitz & Pedersen) |
| `signals/volatility.py` | realised vol, 21/42/63/126 days |
| `signals/regime.py` | panic score: drawdown, vol, vol acceleration, breadth, trend, fixed scales |
| `signals/quality.py` | **not built** (needs fundamentals; meaningless for ETFs) |
| `portfolio/engine.py` | the daily backtest: decide at close t, trade at close t+1, cash earns T-bills, never above 100% invested |
| `validation/costs.py` | half-spread + slippage per ETF, wider before 2013, SEC fee; 2x/3x stress knobs |
| `validation/metrics.py` | CAGR, max DD, Sharpe/Sortino over cash, Calmar, worst 12m, recovery, rolling beat shares |
| `validation/benchmarks.py` | A SPY, B SPY+cash at the same exposure, C simple momentum, D vol-managed SPY, E 60/40 |
| `validation/walk_forward.py` | 7 windows (test 2012-2024), final train/validate split, the sealed OOS |
| `validation/reality_check.py` | White's Reality Check, Hansen's SPA, PBO by CSCV, deflated Sharpe |
| `validation/robustness.py` | gauntlet gates 5-8: parameter, universe, cost and slippage stress |
| `evolution/genome.py` | 26 bounded genes; no leverage gene, no indicator lengths to mine |
| `evolution/gauntlet.py` | hard gates (CAGR > SPY, DD < SPY, trades, exposure >= 50%, worst 12m) then Robbie's 35/25/15/15/10 score; the result ladder |
| `evolution/autopsy.py` | best/worst periods, max DD anatomy, regime table, streaks, failure tags, layer ablations |
| `evolution/descendants.py` | top 10 -> 10 children each, 6 aimed at the parent's failure tags |
| `evolution/search.py` | the GA: discovery on TRAIN, selection on VALIDATE, every genome counted |

## Building on the earlier GA

The "idler" fix lives on: `src/trading/backtest.py`'s fitness uses the dual
hurdle from `hive_mind/lock.py` (beat the benchmark *and* cash). The GA that
found the idler loophole (`/Users/robbie/trade-bots-hive/hive_mind/`) is on
the Mac and is in neither this repo nor the shared Drive exports, so this
package reimplements the lesson rather than importing the code: Sharpe is
over cash, a cash book scores zero, and the gates demand 50% average
exposure, 4 trades a year and a CAGR above SPY before any score counts.

## What is deliberately missing

* Stock quality/value layers: need point-in-time fundamentals (EDGAR filing
  dates) and a stock history that includes delisted names. Neither is
  reachable from the cloud session; see `data/fundamentals.py`.
* Options overlay: only after the core has a result (Robbie's rule 19).
* ML second stage (Qlib/FinRL): after the rule-based baseline.
