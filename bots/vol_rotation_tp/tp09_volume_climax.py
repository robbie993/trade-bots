"""Volatility rotation, Treasuries, with a take profit: Volume Climax.

Let volume say when the move is done. It holds until a blow-off: an up bar on 4x volume, at least 4 ATR in profit. An 8-ATR trailing stop catches a trend that ends quietly instead.

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
    "take_profit": 'volume_climax',
    "tp_climax_rvol": 4.0,
    "tp_arm_atr": 4.0,
    "tp_lookback": 128,
    "tp_trail_atr": 8.0,
    "bars_per_year": vol_rotation.EQUITY_15M_BARS_PER_YEAR,
}


def propose(context):
    return vol_rotation.propose(context, PARAMS)
