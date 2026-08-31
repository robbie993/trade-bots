# PREREG — does scanning the whole market beat a fixed universe?

**Written 2026-08-31, before any arm has been run.** Everything below is fixed
in advance. If a criterion is changed after seeing a number, that fact goes in
the results section in the same words as this one.

## The question

The village trades 36 symbols chosen by hand in `config/firm_config.yaml`. A
scanner can only ever look at that same list (`spec.universe or
market.symbols`), so it cannot discover anything the firms were not already
pointed at. Alpaca's screener returns market-wide gainers, losers and most
actives in a single call, which makes whole-market scanning cheap enough to
test.

Two things are being asked, and only the second one decides anything:

1. Should a discovered name be tradeable immediately, or only a candidate?
2. **Does discovery beat a fixed universe at all** — and does it beat picking
   names at random?

## Why the random arm is not optional

`scanner_bot`'s hand-written universe in the sibling repo was measured at
**+26.9%/yr of pure hindsight**: it looked skilful and was mostly a list
written after the fact. The same repo's four-way stack tied a random-name
control at t=+0.79 and was dropped.

A fixed universe is a hindsight channel. A movers universe is a momentum
channel. Both can look good against nothing. Arm 4 is what tells them apart.

## Arms

All four run over the same window, on the same bar resolution, with the same
costs, the same risk limits and the same firms. Only the universe differs.

| arm | universe |
|---|---|
| **1 — Fixed** | the current 36 symbols. The incumbent. |
| **2 — Movers, gated** | screener → liquidity gate → *candidates*; a firm may adopt one, and the adoption is logged |
| **3 — Movers, open** | screener → liquidity gate → tradeable on the same bar |
| **4 — Random control** | names drawn at random from the same post-gate pool as arms 2 and 3, same count, same rebalance cadence |

Arm 4 must be drawn from the **post-gate** pool. Drawing from all US equities
would compare a liquidity-filtered arm against an unfiltered one and measure
the filter rather than the discovery.

## The gate

From `option_liquidity_gate.py` and `build_universe.py` in the sibling repo,
imported rather than rewritten:

- tradable on Alpaca, NYSE or NASDAQ, symbol ≤ 5 characters
- not a fund/ETF/ETN by name keyword
- minimum price and minimum median dollar volume, both fixed here before any
  run: **price ≥ $5**, **median dollar volume ≥ $10M/day over the 20 bars
  preceding entry**

Twenty bars *preceding entry*, never including it. Ranking a name on volume
that includes the day it moved is the hindsight channel this whole document
exists to avoid.

The raw screener is mostly untradeable: a live call on 2026-08-31 returned
`MIACW +240%`, `GFAIW +140%`, `SAIHW +97%` — warrants and microcaps whose
spreads exceed the move. Any arm that trades those is measuring a fill that
would not have happened.

## Metric

Primary: **net return after costs**, versus arm 4, over the common window.

Secondary, reported always, never promoted to primary after the fact:
max drawdown, Sharpe on the bar the arms were sampled at, turnover, and the
count of distinct names actually traded.

## Drop-dead criteria

Fixed now:

- **If neither arm 2 nor arm 3 beats arm 4 at p < 0.0125, discovery is dropped
  and the fixed universe stays.** 0.0125 is 0.05/4 — four arms is four looks,
  and `research.PValueLedger` records every one of them.
- If arms 2 and 3 differ by less than their own run-to-run spread, the A/B
  question is **unanswered**, and the answer is "unanswered" rather than the
  higher number.
- If fewer than 30 closed trades occur in any arm, that arm reports
  **insufficient data** and is not ranked. The village has produced 28 closed
  trade bars in three weeks, so this is the most likely outcome of a forward
  test and the reason the primary run is a backtest.

## Known biases, stated before the result

- **Survivorship.** The candidate pool is a *current* Alpaca asset snapshot, so
  names delisted during the window are absent. Ranking is point-in-time;
  candidacy is not. Inherited from `build_universe.py`, which documents the
  same limit.
- **The screener is not point-in-time.** Alpaca's movers endpoint returns
  *today's* movers. A backtest cannot ask what the movers were on a past date,
  so arms 2–4 in the backtest must reconstruct movers from historical bars —
  and that reconstruction is not guaranteed to match what the live endpoint
  would have said.
- **Sample size.** 28 closed-trade bars forward. Any forward comparison of four
  arms on that is noise, and will be reported as noise.

## What would make me wrong

If arm 2 or 3 wins on net return but the win is carried by fewer than five
names, it is a story about those names and not about discovery. That check runs
before any headline number is written.
