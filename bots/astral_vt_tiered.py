"""ASTRAL VT 7: TIERED, DOUBLING.

A third at 8, 16 and 32 ATR times short x long participation: the last third can run to 288 ATR.

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

LADDER = ((8.0, 2 / 3), (16.0, 1 / 3), (32.0, 0.0))


def targets(context, symbol, read, entry, basket):
    t = _trend(context, symbol)
    return [(entry * (1 + atrs * t * read["atr_pct"]), keep) for atrs, keep in LADDER]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
