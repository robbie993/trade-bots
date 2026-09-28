"""ASTRAL RUN 8: SHORT TIMES LONG VOLUME.

8 ATR times 16-bar participation times 78-bar participation, each 1x to 3x: 8 to 72 ATR. A burst on top of volume that has been building for days gets the farthest target.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 8.0
LONG_BARS = 78


def targets(context, symbol, read, entry, basket):
    short = participation(context, symbol)
    long = participation(context, symbol, recent_bars=LONG_BARS)
    return [(entry * (1 + BASE_ATRS * short * long * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
