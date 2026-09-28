"""ASTRAL VS 2: VOLUME-SCALED, SQUARED.

6 ATR times participation squared, so 6 ATR on an ordinary tape, 24 ATR
at 2x and 54 at 3x. The parent's idea is that heavy volume means a move
has further to go; this says it has disproportionately further to go.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 6.0


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + BASE_ATRS * p * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
