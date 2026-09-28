"""ASTRAL VS 8: VOLUME-SCALED ON THE WHOLE BASKET.

Participation is averaged across every name in the basket, not just the
one held, with a 10 ATR base: 10 to 30 ATR. When the whole group is
being traded heavily, the sector is moving, and a sector move carries
further than one name's.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 10.0


def targets(context, symbol, read, entry, basket):
    names = list(basket) or [symbol]
    p = sum(participation(context, s) for s in names) / len(names)
    return [(entry * (1 + BASE_ATRS * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
