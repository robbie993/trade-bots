"""Volatility rotation: Treasuries.

Long, long-intermediate and intermediate Treasury ETFs: one rate curve at three durations.
Volatility here is low and volume is steady, so it takes less volume to confirm a move and the
position is sized to a much lower vol target.

Every bar (15 minutes, with TRADE_BAR_TIMEFRAME=15m) this bot ranks the group
on momentum per unit of volatility, holds the leader, and moves in and
out a tranche at a time, only on bars that traded on real volume. The logic
is in src/trading/vol_rotation.py and is shared by all ten bots in this
folder. Only the tickers and the numbers below differ.
"""

from src.trading import vol_rotation

UNIVERSE = ['TLT', 'TLH', 'IEF']

# Anything left out uses the default in vol_rotation.Params.
PARAMS = {
    "target_vol": 0.12,
    "rvol_in": 1.1,
    "rvol_out": 1.1,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
