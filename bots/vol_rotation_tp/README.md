# Ten take-profit iterations of the Treasuries bot

`bots/vol_rotation/07_treasuries.py` came top of the ten rotation bots on five
synthetic seeds, with the best average return (-1.16%, the next best -2.03%)
and the best Sharpe. On synthetic bars "top" mostly means it lost the least.
It runs the smallest positions, so it pays the least in fees.
Each file here is that bot with one change, a take-profit rule, and each rule
is a different idea of where to take the profit.

| # | iteration | where the profit is taken |
|---|---|---|
| 01 | ATR Target | 12 ATR above entry |
| 02 | Volatility Target | a 2.5-sigma move over two weeks of bars |
| 03 | 4R Structure Target | 4x the distance from entry to the swing low; stop at that low |
| 04 | Swing High Breakout | 4 ATR past the nine-session high from before the run |
| 05 | Measured Move | 2x the range from before the run, projected from entry |
| 06 | VWAP Band | 4 standard deviations above the 240-bar VWAP |
| 07 | Chandelier Trail | no ceiling: trail 6 ATR under the high once 3 ATR up |
| 08 | Profit Ladder | a third at 5R, a third at 10R, the last third trailed |
| 09 | Volume Climax | into a 4x-volume up bar once 4 ATR up; 8 ATR trail behind it |
| 10 | Fibonacci Extension | the 2.618 extension of the leg into entry |

The rules are in `src/trading/take_profit.py`.

## What changed, besides the target

Before this, the bot had no take-profit. Every exit was a way of *leaving*:
rotated out, heavy selling under VWAP, momentum flipping negative, a volatility
spike. All of them fire on a winner as readily as on a loser, so wins stayed
small: $10.77 on average.

So in all ten iterations, **a winner ignores those exits**. While a position
is above its average entry, only the take-profit rule can close it. When it
falls back under entry, every original exit applies again. A winner held past
its rotation also keeps its slot, so a new leader waits instead of doubling
the book.

## Results

Five seeds of synthetic 15-minute bars, 1,500 bars each, $25,000 per run,
fees and slippage on. `python scripts/compare_take_profit.py` reproduces it.

| iteration | return | avg win | vs baseline | avg loss | best win | win rate |
|---|---|---|---|---|---|---|
| baseline (no take profit) | -1.05% | $10.77 | 1x | -$6.62 | $61 | 29.4% |
| 01 ATR Target | -0.79% | $240.72 | 22x | -$7.31 | $327 | 1.7% |
| 02 Volatility Target | -0.78% | $287.74 | 27x | -$7.31 | $423 | 1.3% |
| 03 4R Structure Target | -0.62% | $140.37 | 13x | -$7.05 | $222 | 3.3% |
| 04 Swing High Breakout | -0.96% | $111.11 | 10x | -$7.14 | $256 | 4.2% |
| 05 Measured Move | -0.72% | $125.97 | 12x | -$7.05 | $218 | 3.4% |
| 06 VWAP Band | -1.01% | $102.39 | 10x | -$7.15 | $102 | 0.4% |
| 07 Chandelier Trail | -0.42% | $108.25 | 10x | -$7.05 | $189 | 2.3% |
| 08 Profit Ladder | -0.65% | $37.39 | 3.5x | -$7.21 | $102 | 11.0% |
| 09 Volume Climax | -0.45% | $151.56 | 14x | -$7.11 | $173 | 1.5% |
| 10 Fibonacci Extension | -0.79% | $113.86 | 11x | -$7.24 | $257 | 4.7% |

A "win" is one profitable sale. The ladder sells in thirds, so one trade can
be up to three wins, which is part of why its average is the lowest.

**Wins got much bigger. Wins also got much rarer.** The win rate falls from
29% to between 0.4% and 11%. Holding for a distant target means most trades
that were up at some point drift back under entry, and then the original
exits close them as small losses. That is the price of a bigger target, and
the table does not hide it.

**Every iteration lost less than the baseline, and none made money.** That is
what synthetic bars can show. They are a random walk with random volume and
no correlation between tickers, so no strategy has an edge on them. What they
do show is that each rule behaves as designed, and what it does to the shape
of the wins and losses. Whether a bigger take-profit pays on real Treasuries
depends on whether real trends run far enough to reach it. Only real bars can
answer that:

```bash
TRADE_DATA_SOURCE=alpaca python scripts/compare_take_profit.py --seeds 1
```

## Two bugs found and fixed on the way

* **A target that runs away.** The first Swing High measured the high over
  recent bars, which included the rally the trade was riding, so the target
  rose with the price and was never reached: zero wins in five runs. Measured
  Move had the same flaw. Its $413 average win came from the range growing
  under it, not from a real target. Both now measure from the bars before the
  run (`before_the_run`), which is where the $111 and $126 above come from.
* **A stop that runs away.** The first structural stop measured the swing low
  over recent bars too. A trade falling through its low kept finding a lower
  one, so the stop slid down with the price and could never fire. It is now
  anchored to the last bar that closed above entry (`swing_low_risk`).

## Running them

Same as the originals, with this folder's firms file:

```bash
export TRADE_FIRMS_CONFIG=config/vol_rotation_tp.yaml
export TRADE_BAR_TIMEFRAME=15m TRADE_TICK_INTERVAL_S=900 TRADE_HISTORY_DAYS=1500
python -m src.main trade init && python -m src.main trade backtest
```

`ASTRAL.md` has all ten as Astral prompts, generated by
`python scripts/vol_rotation_astral.py`.
