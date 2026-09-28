"""Volatility rotation, Treasuries, with a take profit: 4R Structure Target.

Risk is set by the chart, not by volatility: 1R is the drop from entry to the swing low the run started from (at least 1.5 ATR). Aim for 4R. A loss is capped at 1R and a win is four of them, so it can be wrong most of the time and still come out ahead.

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
    "take_profit": 'r_multiple',
    "tp_r": 4.0,
    "tp_lookback": 128,
    "tp_stop_atr": 1.5,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
