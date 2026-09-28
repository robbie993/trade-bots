"""ASTRAL TP 1: R-MULTIPLE. One target at 4R.

R is the distance to the stop, 3 ATR, so the target sits 12 ATR above the
entry and a winner pays four times what a loser costs. The plainest way to
put a take-profit far out: size it off the risk already taken.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run, STOP_ATRS

TARGET_R = 4.0


def targets(context, symbol, read, entry, basket):
    return [(entry * (1 + TARGET_R * STOP_ATRS * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
