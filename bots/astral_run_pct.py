"""ASTRAL RUN 6: A PERCENT, SCALED BY VOLUME.

5% times participation for equities (5 to 15%), 10% times participation for crypto (10 to 30%). A swing-sized move, larger when volume is heavy.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

EQUITY_PCT = 0.05
CRYPTO_PCT = 0.10


def targets(context, symbol, read, entry, basket):
    pct = CRYPTO_PCT if "-" in str(symbol) else EQUITY_PCT
    return [(entry * (1 + pct * participation(context, symbol)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
