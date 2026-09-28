"""ASTRAL RUN 3: PARTICIPATION SQUARED.

6 ATR times participation squared: 6 ATR on a normal tape, 24 at 2x volume, 54 at 3x. Heavy volume buys a disproportionately far target.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 6.0


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + BASE_ATRS * p * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
