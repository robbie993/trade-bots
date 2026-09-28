"""ASTRAL RUN 10: THE LEG, PROJECTED BY VOLUME.

The entry's height above the lowest low of the last 78 bars, projected 2x participation times above the entry: two to six legs. The heavier the volume behind the leg, the more times it is expected to repeat.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

LEG_BARS = 78
LEGS = 2.0


def targets(context, symbol, read, entry, basket):
    lows = [float(x) for x in context.lows(symbol, LEG_BARS)]
    if not lows or entry <= min(lows):
        return []
    leg = entry - min(lows)
    return [(entry + LEGS * participation(context, symbol) * leg, 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
