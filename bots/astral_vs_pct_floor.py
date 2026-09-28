"""ASTRAL VS 6: VOLUME-SCALED WITH A PERCENT FLOOR.

The parent's 6 to 18 ATR, but never under +10% for equities or +20% for
crypto. On a quiet name the ATR rule can put the target a few percent out;
this holds a swing-sized floor under it.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 6.0
EQUITY_FLOOR = 0.10
CRYPTO_FLOOR = 0.20


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    floor = CRYPTO_FLOOR if "-" in str(symbol) else EQUITY_FLOOR
    return [(entry * (1 + max(BASE_ATRS * p * read["atr_pct"], floor)), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
