"""ASTRAL RUN 7: THE WHOLE BASKET'S VOLUME.

12 ATR times participation averaged across all five names: 12 to 36 ATR. A sector being traded heavily is a sector move, and a sector move carries further.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 12.0


def targets(context, symbol, read, entry, basket):
    names = list(basket) or [symbol]
    p = sum(participation(context, s) for s in names) / len(names)
    return [(entry * (1 + BASE_ATRS * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
