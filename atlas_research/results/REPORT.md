# Atlas Core (ETFs, no options): measured results

Generated 2026-10-02T18:46:36+00:00 from `run_core.py`. Data through 2026-07-30. Every number below is a backtest on the data described in `data/prices.py`; none of it is live trading.

## Verdict

**RESEARCH FAILURE** after the frozen OOS (2025-01-03 to 2026-07-30).

Before the OOS: **RESEARCH FAILURE**.

## Walk-forward (the search process, out of sample)

Each window searched TRAIN, picked its champion on VALIDATE and ran it once on TEST. The TEST years are never used to choose anything.

| window | test | Atlas CAGR | SPY CAGR | Atlas max DD | SPY max DD | exposure | passed gates |
|---|---|---|---|---|---|---|---|
| W1 | 2012-2013 | +8.4% | +23.1% | -11.2% | -9.7% | 94% | no: cagr_above_spy, drawdown_below_spy |
| W2 | 2014-2015 | +1.5% | +7.7% | -14.2% | -11.9% | 93% | no: cagr_above_spy, drawdown_below_spy |
| W3 | 2016-2017 | -1.6% | +17.7% | -19.1% | -9.2% | 95% | no: cagr_above_spy, drawdown_below_spy |
| W4 | 2018-2019 | -2.5% | +11.6% | -15.5% | -19.3% | 90% | no: cagr_above_spy |
| W5 | 2020-2021 | +27.3% | +22.8% | -9.7% | -33.7% | 79% | yes |
| W6 | 2022-2023 | -0.5% | +1.3% | -13.5% | -24.5% | 71% | no: cagr_above_spy |
| W7 | 2024-2024 | +6.3% | +25.7% | -9.1% | -8.4% | 99% | no: cagr_above_spy, drawdown_below_spy |

Stitched TEST record 2012-01-04 to 2024-12-31: Atlas CAGR +5.09% vs SPY +14.63%, max DD -24.8% vs -33.7%, Sharpe 0.36 vs 0.83, Calmar 0.20 vs 0.43. Calendar years beating SPY: 2/13. Rolling 3-year windows beating SPY: 15%.

## Final champion

Key `1afa87c3f1`, chosen on train 2002-2022 and validation 2023-2024 (no finalist passed the validation gates).

```
{
 "mom_w21": 0.1780311981281907,
 "mom_w63": 0.4,
 "mom_w126": 0.3120331987189091,
 "mom_w252": 0.5881049084955492,
 "skip_recent": 0,
 "value_w": 0.14384202787846145,
 "lowvol_w": 0.13059560856820182,
 "trend_filter": 2,
 "top_n": 2,
 "inverse_vol": 0,
 "position_cap": 0.9278217100413784,
 "vol_lookback": 21,
 "target_vol": 0.16703992126979383,
 "pw_drawdown": 0.17852171833757358,
 "pw_volatility": 0.8754778118308882,
 "pw_vol_accel": 0.31374751284809677,
 "pw_breadth": 0.8691328189042213,
 "pw_trend": 0.9716572889821583,
 "warn_threshold": 0.3885434732743068,
 "panic_threshold": 0.6206763537956371,
 "warn_exposure": 0.7893594012929787,
 "panic_exposure": 0.41743959969026456,
 "rebalance_days": 5,
 "rebalance_threshold": 0.09931123564171669,
 "stop_rule": 0.0,
 "panic_daily": 0
}
```

### In sample, 2002-2024, against the five benchmarks

| | CAGR | max DD | Sharpe | Sortino | Calmar | worst 12m | longest underwater |
|---|---|---|---|---|---|---|---|
| **Atlas** | +11.5% | -17.3% | 0.78 | 1.09 | 0.67 | -13.0% | 434 days |
| A SPY buy & hold | +9.3% | -55.2% | 0.48 | 0.68 | 0.17 | -47.4% | 1223 days |
| B SPY + cash (same exposure) | +8.2% | -47.4% | 0.48 | 0.68 | 0.17 | -40.1% | 1106 days |
| C simple momentum | +7.2% | -37.5% | 0.43 | 0.59 | 0.19 | -35.0% | 768 days |
| D vol-managed SPY | +8.3% | -28.2% | 0.62 | 0.85 | 0.29 | -22.8% | 861 days |
| E 60/40 SPY/IEF | +7.3% | -32.3% | 0.56 | 0.80 | 0.22 | -28.0% | 704 days |

### Validation, 2023-2024

| | CAGR | max DD | Sharpe | Sortino | Calmar | worst 12m | longest underwater |
|---|---|---|---|---|---|---|---|
| **Atlas** | +2.7% | -12.0% | -0.12 | -0.16 | 0.23 | -6.5% | 282 days |
| A SPY buy & hold | +26.0% | -10.0% | 1.48 | 2.18 | 2.60 | +16.7% | 85 days |
| B SPY + cash (same exposure) | +25.1% | -9.6% | 1.48 | 2.18 | 2.63 | +16.3% | 85 days |
| C simple momentum | +4.1% | -14.9% | 0.00 | 0.00 | 0.27 | -3.9% | 259 days |
| D vol-managed SPY | +21.6% | -9.7% | 1.34 | 1.95 | 2.23 | +15.5% | 93 days |
| E 60/40 SPY/IEF | +15.6% | -8.3% | 1.17 | 1.73 | 1.89 | +10.7% | 98 days |

### Frozen OOS, 2025-01-03 to 2026-07-30 (run once, zero changes)

| | CAGR | max DD | Sharpe | Sortino | Calmar | worst 12m | longest underwater |
|---|---|---|---|---|---|---|---|
| **Atlas** | +42.7% | -11.4% | 1.80 | 2.50 | 3.74 | +50.5% | 69 days |
| A SPY buy & hold | +17.8% | -18.8% | 0.79 | 1.18 | 0.95 | +12.7% | 87 days |
| B SPY + cash (same exposure) | +14.6% | -14.3% | 0.79 | 1.18 | 1.02 | +10.9% | 85 days |
| C simple momentum | +36.2% | -27.1% | 1.10 | 1.44 | 1.34 | +35.5% | 126 days |
| D vol-managed SPY | +9.7% | -13.6% | 0.49 | 0.67 | 0.71 | +5.7% | 131 days |
| E 60/40 SPY/IEF | +12.7% | -10.5% | 0.79 | 1.18 | 1.21 | +9.8% | 72 days |

T-bill rates for (2025, 2026) are estimates.

## Gauntlet (in sample 2002-2024)

* Gate 4, stability: largest single year's share of total excess return 211%; calendar years beating SPY 12/23.
* Gate 5, parameters: 71 single-gene moves of +/-10-20%; 99% still beat SPY on both CAGR and drawdown.
* Gate 6, universe: 87% of drop-one-ETF universes beat SPY on both.
* Gates 7-8, costs and slippage:
  * base: CAGR +11.5%, max DD -17.3%, costs 83 bp/yr
  * costs_2x: CAGR +11.3%, max DD -17.3%, costs 107 bp/yr
  * costs_3x: CAGR +11.0%, max DD -17.5%, costs 131 bp/yr
  * slippage_2x: CAGR +10.9%, max DD -17.5%, costs 139 bp/yr
  * slippage_3x: CAGR +10.3%, max DD -18.3%, costs 194 bp/yr
  * both_3x: CAGR +9.8%, max DD -19.0%, costs 243 bp/yr
* Gate 9, regimes (annualised):

| regime | days | Atlas | SPY | exposure |
|---|---|---|---|---|
| bull | 3781 | +21.0% | +27.3% | 93% |
| bear | 700 | -5.3% | -40.2% | 30% |
| high_vol | 678 | +0.0% | +2.4% | 41% |
| low_vol | 2559 | +9.8% | +13.7% | 97% |
| sideways | 1586 | +2.3% | +11.7% | 82% |
| recovery | 99 | +21.1% | +143.0% | 73% |

## Autopsy

Failure tags: missed_rebound, bull_lag, whipsaw.
Worst drawdown -17.3% from 2016-08-01 to 2016-11-14, recovered 2018-01-23; 91% invested on the way down.
Longest run of months behind SPY: 7; ahead: 6.

Worst 3-month stretches vs SPY: 2009-03-10 to 2009-06-08 -28.5%; 2020-03-24 to 2020-06-22 -27.8%; 2019-09-05 to 2019-12-03 -20.3%

Best: 2008-08-25 to 2008-11-20 +70.5%; 2019-12-20 to 2020-03-23 +44.4%; 2007-12-07 to 2008-03-10 +39.4%

Excess return over SPY by calendar year: 2002 +21%, 2003 -2%, 2004 +5%, 2005 +4%, 2006 +8%, 2007 +12%, 2008 +55%, 2009 -20%, 2010 +16%, 2011 +9%, 2012 -5%, 2013 -6%, 2014 -6%, 2015 +1%, 2016 -20%, 2017 -14%, 2018 +2%, 2019 -30%, 2020 +16%, 2021 -0%, 2022 +30%, 2023 -26%, 2024 -24%

What each layer contributed (switching it off, 2002-2024):

* value: CAGR +1.44%, max DD improved by +5.5 pts
* lowvol: CAGR +1.96%, max DD improved by +4.1 pts
* trend_filter: CAGR +0.24%, max DD improved by +0.8 pts
* vol_target: CAGR -0.31%, max DD improved by +4.2 pts
* regime: CAGR +0.28%, max DD improved by +1.3 pts

## Overfitting checks (final search)

* White's Reality Check p = 0.43, Hansen SPA p = 0.46, over 548 genomes vs SPY on the train days (small p would mean the best genome beats SPY beyond search luck).
* Probability of backtest overfitting (CSCV, 16 blocks): 58%. In-sample winner's median Sharpe 0.99, same genome out of sample 0.70.
* Deflated Sharpe: 100% probability the champion's Sharpe over cash is real after 548 trials (luck alone would give 0.31).

## Trial counts

```
{
 "total_genomes_evaluated": 4472,
 "searches": 8,
 "generations_per_search": 5,
 "restarts": 0,
 "genes": 26,
 "signal_families_tested": [
  "momentum (4 fixed lookbacks)",
  "5-year reversal value",
  "low volatility",
  "trend filter",
  "volatility target",
  "panic regime (5 fixed inputs)",
  "stop rule"
 ],
 "note": "every genome the GA ever evaluated is counted; no run was discarded"
}
```
