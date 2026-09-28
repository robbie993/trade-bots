"""ASTRAL VT 3: RAISE THE CAPS.

The parent's 8 ATR base, but each participation may reach 5x instead of 3x: 8 to 200 ATR. The target only goes further when volume really is extreme.

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
CAP = 5.0


def targets(context, symbol, read, entry, basket):
    return [(entry * (1 + BASE_ATRS * _trend(context, symbol, cap=CAP) * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
