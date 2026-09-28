"""ASTRAL TP 3: MEASURED MOVE, TWICE OVER.

The leg is the entry's height above the lowest low of the last three
sessions (78 bars): the move that made this name a leader. The target
projects that leg twice more above the entry. Classic measured-move
projection, doubled.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

LEG_BARS = 78
PROJECTIONS = 2.0


def targets(context, symbol, read, entry, basket):
    lows = [float(x) for x in context.lows(symbol, LEG_BARS)]
    if not lows:
        return []
    leg = entry - min(lows)
    if leg <= 0:
        return []
    return [(entry + PROJECTIONS * leg, 0.0)]


def propose(context):
    return run(context, take_profit=targets)
