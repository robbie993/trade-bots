"""ASTRAL TP 2: THREE DAYS' EXPECTED MOVE.

The target is three times the one-session, one-sigma move: bar volatility
scaled by the square root of the bars in a session (26 for equities, 96 for
crypto). An intraday entry held for a multi-session move, sized by what the
name normally does in a day rather than in fifteen minutes.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

import math

from bots.astral import run, bars_per_session

SIGMAS = 3.0


def targets(context, symbol, read, entry, basket):
    daily = read["slow_vol"] * math.sqrt(bars_per_session(symbol))
    return [(entry * math.exp(SIGMAS * daily), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
