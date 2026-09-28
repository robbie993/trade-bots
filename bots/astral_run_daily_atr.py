"""ASTRAL RUN 5: DAILY ATRS.

3 daily ATRs times participation, a daily ATR being the bar ATR times the square root of the bars in a session. About 15 to 46 bar-ATRs on equities, 29 to 88 on crypto.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

import math

from bots.astral import run, RUN_TRAIL, bars_per_session
from bots.astral_tp_volume_scaled import participation

DAILY_ATRS = 3.0


def targets(context, symbol, read, entry, basket):
    daily = read["atr_pct"] * math.sqrt(bars_per_session(symbol))
    return [(entry * (1 + DAILY_ATRS * daily * participation(context, symbol)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
