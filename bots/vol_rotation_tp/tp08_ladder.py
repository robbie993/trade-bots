"""Volatility rotation, Treasuries, with a take profit: Profit Ladder.

Scale out the way it scaled in. A third comes off at 5R and a third at 10R, and the last third runs on a 6-ATR trailing stop.

This is bots/vol_rotation/07_treasuries.py, the best of the ten on the
synthetic benchmark, with one change: a take-profit rule. While a trade is
above its average entry, only that rule can close it. The rotation,
distribution, momentum and volatility exits are ignored until the price falls
back under entry. The rules are in src/trading/take_profit.py.
"""

from src.trading import vol_rotation

UNIVERSE = ['TLT', 'TLH', 'IEF']

PARAMS = {
    "target_vol": 0.12,
    "rvol_in": 1.1,
    "rvol_out": 1.1,
    "take_profit": 'ladder',
    "tp_r": 10.0,
    "tp_stop_atr": 1.5,
    "tp_lookback": 128,
    "tp_trail_atr": 6.0,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
