"""ASTRAL TP 4: RANGE PROJECTION.

Take the whole range the village holds for the name (about 250 bars: ten
equity sessions, or two and a half days of crypto) and project its height
above its high. A leader breaking out of its range is priced to travel
the full range again.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

def targets(context, symbol, read, entry, basket):
    highs = [float(h) for h in context.highs(symbol)]
    lows = [float(x) for x in context.lows(symbol)]
    if not highs or not lows:
        return []
    top, bottom = max(highs), min(lows)
    return [(top + (top - bottom), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
