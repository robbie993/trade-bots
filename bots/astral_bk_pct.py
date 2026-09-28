"""ASTRAL BK 6: A PERCENT.

15% times the basket's volume trend for equities (15 to 135%), 30% for crypto (30 to 270%).

An iteration of astral_vt_basket (12 to 108 ATR), the best of the
astral_vt_* round in scripts/astral_backtest.py, with its target pushed
further out. Same trail (RUN_TRAIL), so only the target differs. See
`run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
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

EQUITY_PCT = 0.15
CRYPTO_PCT = 0.30


def targets(context, symbol, read, entry, basket):
    pct = CRYPTO_PCT if "-" in str(symbol) else EQUITY_PCT
    return [(entry * (1 + pct * _basket_trend(context, symbol, basket)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
