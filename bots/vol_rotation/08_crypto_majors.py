"""Volatility rotation: Crypto Majors.

The three largest liquid coins. Crypto trades around the clock, so volatility is annualised
over 96 bars a day, 365 days a year. It spikes more often, so the spike threshold is looser.

Every bar (15 minutes, with TRADE_BAR_TIMEFRAME=15m) this bot ranks the group
on momentum per unit of volatility, holds the leader, and moves in and
out a tranche at a time, only on bars that traded on real volume. The logic
is in src/trading/vol_rotation.py and is shared by all ten bots in this
folder. Only the tickers and the numbers below differ.
"""

from src.trading import vol_rotation

UNIVERSE = ['BTC-USD', 'ETH-USD', 'SOL-USD']

# Anything left out uses the default in vol_rotation.Params.
PARAMS = {
    "target_vol": 0.6,
    "max_vol_ratio": 2.5,
    "bars_per_year": vol_rotation.CRYPTO_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
