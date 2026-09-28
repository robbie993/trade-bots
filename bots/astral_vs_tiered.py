"""ASTRAL VS 7: VOLUME-SCALED, TIERED.

Sells a third at the parent's target (6 ATR x participation), a third at
twice it and the last third at three times it: up to 54 ATR for the final
third. The parent's in-thirds entry, mirrored on the way out, with the
last third left to run a long way.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

LADDER = ((6.0, 2 / 3), (12.0, 1 / 3), (18.0, 0.0))


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + atrs * p * read["atr_pct"]), keep) for atrs, keep in LADDER]


def propose(context):
    return run(context, take_profit=targets)
