"""ASTRAL VT 4: SQUARE THE BURST.

8 ATR times short participation squared times long: 8 to 216 ATR. The recent burst counts twice.

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

BASE_ATRS = 8.0


def targets(context, symbol, read, entry, basket):
    short = participation(context, symbol)
    long = participation(context, symbol, recent_bars=LONG_BARS)
    return [(entry * (1 + BASE_ATRS * short * short * long * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
