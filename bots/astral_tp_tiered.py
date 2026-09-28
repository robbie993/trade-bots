"""ASTRAL TP 9: TIERED, A THIRD AT A TIME.

The mirror of how it gets in. Astral builds in thirds on volume, and this
gets out in thirds on price: a third at 2R, a third at 4R, the last at 6R
(R = the 3-ATR stop, so 6, 12 and 18 ATR). It banks something early and
still has a third riding to 18 ATR, the farthest single level of the ten.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run, STOP_ATRS

LADDER = ((2.0, 2 / 3), (4.0, 1 / 3), (6.0, 0.0))


def targets(context, symbol, read, entry, basket):
    risk = STOP_ATRS * read["atr_pct"]
    return [(entry * (1 + r * risk), keep) for r, keep in LADDER]


def propose(context):
    return run(context, take_profit=targets)
