"""ASTRAL BK 5: DAILY ATRS.

3 daily ATRs times the basket's volume trend, a daily ATR being the bar ATR times the square root of the bars in a session: about 15 to 138 bar-ATRs on equities, 29 to 265 on crypto.

An iteration of astral_vt_basket (12 to 108 ATR), the best of the
astral_vt_* round in scripts/astral_backtest.py, with its target pushed
further out. Same trail (RUN_TRAIL), so only the target differs. See
`run` in bots/astral.py. Not in the village.
"""

import math

from bots.astral import RUN_TRAIL, run
from bots.astral import bars_per_session
from bots.astral_tp_volume_scaled import participation

LONG_BARS = 78


def _basket_trend(context, symbol, basket, recent_bars=16, cap=3.0, combine=None):
    """The parent's volume term: short and long participation, each averaged
    across the basket (or combined with ``combine``), multiplied."""
    names = list(basket) or [symbol]
    combine = combine or (lambda xs: sum(xs) / len(xs))
    short = combine([participation(context, s, recent_bars=recent_bars, cap=cap) for s in names])
    long = combine([participation(context, s, recent_bars=LONG_BARS, cap=cap) for s in names])
    return short * long

DAILY_ATRS = 3.0


def targets(context, symbol, read, entry, basket):
    daily = read["atr_pct"] * math.sqrt(bars_per_session(symbol))
    return [(entry * (1 + DAILY_ATRS * daily * _basket_trend(context, symbol, basket)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
