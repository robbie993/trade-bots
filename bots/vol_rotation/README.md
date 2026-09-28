# Ten volatility-rotation bots

Ten firms, each rotating within one group of correlated tickers on 15-minute
bars, and scaling in and out a tranche at a time on volume.

| # | bot | universe | holds | vol target |
|---|---|---|---|---|
| 01 | Index ETFs | SPY QQQ IWM DIA | 1 | 20% |
| 02 | Semiconductors | NVDA AMD AVGO SMH | 1 | 50% |
| 03 | Megacap Tech | AAPL MSFT GOOGL META AMZN | 2 | 35% |
| 04 | Precious Metals | GLD SLV GDX | 1 | 30% |
| 05 | Energy | XLE XOM CVX XOP | 1 | 30% |
| 06 | Banks | JPM BAC WFC XLF | 1 | 30% |
| 07 | Treasuries | TLT TLH IEF | 1 | 12% |
| 08 | Crypto Majors | BTC-USD ETH-USD SOL-USD | 1 | 60% |
| 09 | Crypto Equities | COIN MSTR MARA RIOT | 1 | 80% |
| 10 | Homebuilders | ITB XHB DHI LEN | 1 | 30% |

## How they trade

The logic lives once, in `src/trading/vol_rotation.py`. Each file here is a
universe and a few numbers. Every bar:

1. **Rank.** Each ticker is scored on momentum per unit of volatility: its
   16-bar log return divided by the volatility it took to get there. A ticker
   whose short-term volatility has spiked past 2x its own baseline is not
   eligible. The best-scoring eligible ticker is the target, and a holding
   gets a head start in the ranking so a near-tie does not cause a rotation.
2. **Size.** A full position is 25% of equity, scaled down when the ticker
   runs hotter than the bot's vol target, and split into three tranches.
3. **Scale in on volume.** A target adds a tranche only on an up bar, above
   VWAP, trading at 1.2x its normal volume, and adds two on a 2x surge. A quiet
   up bar adds nothing.
4. **Scale out on volume.** A ticker rotated out is sold a tranche a bar, two
   when volume is heavy, and all of it on a heavy down bar. A target that
   prints a heavy down bar under VWAP gives back a tranche. Negative momentum
   or a volatility spike sells everything at once.

## Running them here

They need 15-minute bars, which is a village-wide setting:

```bash
export TRADE_FIRMS_CONFIG=config/vol_rotation.yaml   # these ten, not your usual firms
export TRADE_BAR_TIMEFRAME=15m
export TRADE_TICK_INTERVAL_S=900                     # tick once a bar
export TRADE_HISTORY_DAYS=1500                       # read as bars once intraday
export TRADE_DATA_SOURCE=alpaca                      # stocks and crypto; needs free keys

python -m src.main trade init
python -m src.main trade backtest
```

`TRADE_DATA_SOURCE=yahoo` works without keys, but Yahoo serves only the last
60 days of 15-minute bars, about 1,100 bars of stock data. Alpaca goes back
further, and it is the only feed here that covers stocks and crypto together.

`trade init` creates them on the paper venue with $25,000 each, and they trade
paper money from the next `trade tick` (or `trade run`, one tick every
`TRADE_TICK_INTERVAL_S`). Every order they propose is reviewed by the risk
manager, the conscience and the kill switch, like any firm's. A live venue
still needs its own approval.

## Running them in Astral

`ASTRAL.md` has all ten as prompts to paste into Astral's strategy builder,
generated from these files by `python scripts/vol_rotation_astral.py`.

## What has not been shown

These have been run end to end only on the synthetic feed, which is a random
walk with random volume and no correlation between tickers. On that feed no
momentum or volume signal can work, and they lose a little to fees, as they
should. Nothing here says they make money on real markets. Backtest them on
real bars, here or in Astral, before funding one.
