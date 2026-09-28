"""ASTRAL TP 8: FIBONACCI 2.618 EXTENSION.

The swing is the lowest low to the highest high of the last three sessions
(78 bars). The target is 1.618 swings above the swing high, which is the
2.618 extension measured from the low. The level chart traders watch
for where a trend leg exhausts.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

SWING_BARS = 78
EXTENSION = 1.618


def targets(context, symbol, read, entry, basket):
    highs = [float(h) for h in context.highs(symbol, SWING_BARS)]
    lows = [float(x) for x in context.lows(symbol, SWING_BARS)]
    if not highs or not lows:
        return []
    top, bottom = max(highs), min(lows)
    return [(top + EXTENSION * (top - bottom), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
