"""Volatility rotation: Energy.

The integrated majors, the sector ETF and the exploration-and-production ETF, all tied to
crude. XOP is the high-beta end of the group.

Every bar (15 minutes, with TRADE_BAR_TIMEFRAME=15m) this bot ranks the group
on momentum per unit of volatility, holds the leader, and moves in and
out a tranche at a time, only on bars that traded on real volume. The logic
is in src/trading/vol_rotation.py and is shared by all ten bots in this
folder. Only the tickers and the numbers below differ.
"""

from src.trading import vol_rotation

UNIVERSE = ['XLE', 'XOM', 'CVX', 'XOP']

# Anything left out uses the default in vol_rotation.Params.
PARAMS = {
    "target_vol": 0.3,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
