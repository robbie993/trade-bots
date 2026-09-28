"""ASTRAL VS 1: VOLUME-SCALED x2: DOUBLE THE BASE.

The parent's rule with the base doubled: 12 ATR times participation
(1x to 3x), so 12 to 36 ATR where the parent reached 6 to 18. The
plainest way to push the parent's target out.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 12.0


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + BASE_ATRS * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
