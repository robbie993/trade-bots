"""ASTRAL TP 10: STATISTICAL EXHAUSTION.

A leader is picked for a large 16-bar move measured in its own sigmas; this
sells when that move reaches 4 sigma. Entries need only 0.5. The target is
the price at which the 16-bar z would read 4, a move rare enough that
holding past it is betting on the tail.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

import math

from bots.astral import run

EXHAUSTION_Z = 4.0
MOMENTUM_BARS = 16


def targets(context, symbol, read, entry, basket):
    closes = [float(c) for c in context.closes(symbol, MOMENTUM_BARS + 1)]
    if len(closes) < MOMENTUM_BARS + 1:
        return []
    anchor = closes[0]
    return [(anchor * math.exp(EXHAUSTION_Z * read["slow_vol"] * math.sqrt(MOMENTUM_BARS)), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
