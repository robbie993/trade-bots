"""Volatility rotation, Treasuries, with a take profit: ATR Target.

A fixed target 12 average true ranges above entry. The plainest way to ask for a bigger win: name it, and wait for it.

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
    "take_profit": 'atr_target',
    "tp_atr": 12.0,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
