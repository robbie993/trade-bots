"""ASTRAL BK 10: THE BASKET'S WIDEST ATR.

12 ATR times the basket's volume trend, but the ATR is the widest in the basket rather than the held name's own: a quiet leader in a wild group is priced to move like the group.

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

BASE_ATRS = 12.0


def targets(context, symbol, read, entry, basket):
    atr = max([r["atr_pct"] for r in basket.values() if r and r.get("atr_pct")] or [read["atr_pct"]])
    return [(entry * (1 + BASE_ATRS * _basket_trend(context, symbol, basket) * atr), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
