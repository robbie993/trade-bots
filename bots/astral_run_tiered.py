"""ASTRAL RUN 4: TIERED, A THIRD AT A TIME.

A third at 6, 12 and 18 ATR times participation: the last third can run to 54 ATR. Banks some early, lets the rest run.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

LADDER = ((6.0, 2 / 3), (12.0, 1 / 3), (18.0, 0.0))


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + atrs * p * read["atr_pct"]), keep) for atrs, keep in LADDER]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
