"""Volatility rotation: Megacap Tech.

Five megacaps that dominate the index and move with it. Five names is a wide enough field to
hold the best two at once.

Every bar (15 minutes, with TRADE_BAR_TIMEFRAME=15m) this bot ranks the group
on momentum per unit of volatility, holds the leaders, and moves in and
out a tranche at a time, only on bars that traded on real volume. The logic
is in src/trading/vol_rotation.py and is shared by all ten bots in this
folder. Only the tickers and the numbers below differ.
"""

from src.trading import vol_rotation

UNIVERSE = ['AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN']

# Anything left out uses the default in vol_rotation.Params.
PARAMS = {
    "hold": 2,
    "target_vol": 0.35,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
