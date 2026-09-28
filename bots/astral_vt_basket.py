"""ASTRAL VT 9: THE WHOLE BASKET'S VOLUME TREND.

12 ATR times the basket-average short participation times the basket-average long: 12 to 108 ATR. The sector's volume, not one name's.

An iteration of astral_run_volume_trend (8 to 72 ATR), the best of the
astral_run_* round in scripts/astral_backtest.py, with its target pushed
further out. Same trail as the parent (RUN_TRAIL: 4 ATR off the last
session's high), so only the target differs. See `run` in
bots/astral.py. Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

LONG_BARS = 78


def _trend(context, symbol, cap=3.0):
    """The parent's volume term: 16-bar times 78-bar participation."""
    return (participation(context, symbol, cap=cap)
            * participation(context, symbol, recent_bars=LONG_BARS, cap=cap))

BASE_ATRS = 12.0


def targets(context, symbol, read, entry, basket):
    names = list(basket) or [symbol]
    short = sum(participation(context, s) for s in names) / len(names)
    long = sum(participation(context, s, recent_bars=LONG_BARS) for s in names) / len(names)
    return [(entry * (1 + BASE_ATRS * short * long * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
