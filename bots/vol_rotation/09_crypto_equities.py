"""Volatility rotation: Crypto Equities.

An exchange, a bitcoin treasury and two miners: stocks that trade as leveraged bitcoin during
market hours. Surges are common here, so it takes a bigger one to double a tranche.

Every bar (15 minutes, with TRADE_BAR_TIMEFRAME=15m) this bot ranks the group
on momentum per unit of volatility, holds the leader, and moves in and
out a tranche at a time, only on bars that traded on real volume. The logic
is in src/trading/vol_rotation.py and is shared by all ten bots in this
folder. Only the tickers and the numbers below differ.
"""

from src.trading import vol_rotation

UNIVERSE = ['COIN', 'MSTR', 'MARA', 'RIOT']

# Anything left out uses the default in vol_rotation.Params.
PARAMS = {
    "target_vol": 0.8,
    "rvol_surge": 2.5,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
