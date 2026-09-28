"""ASTRAL VS 4: VOLUME-SCALED IN DAILY ATRS.

The parent counts in fifteen-minute ATRs. This counts in daily ones: 2
daily ATR times participation, where a daily ATR is the bar ATR scaled by
the square root of the bars in a session. About 10 to 31 bar-ATRs on
equities and 20 to 59 on crypto, which trades around the clock.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

import math

from bots.astral import run, bars_per_session
from bots.astral_tp_volume_scaled import participation

DAILY_ATRS = 2.0


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    daily = read["atr_pct"] * math.sqrt(bars_per_session(symbol))
    return [(entry * (1 + DAILY_ATRS * daily * p), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
