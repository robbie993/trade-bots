"""ASTRAL VT 6: IN DAILY ATRS.

2 daily ATRs times short x long participation, a daily ATR being the bar ATR times the square root of the bars in a session: about 10 to 92 bar-ATRs on equities and 20 to 176 on crypto.

An iteration of astral_run_volume_trend (8 to 72 ATR), the best of the
astral_run_* round in scripts/astral_backtest.py, with its target pushed
further out. Same trail as the parent (RUN_TRAIL: 4 ATR off the last
session's high), so only the target differs. See `run` in
bots/astral.py. Not in the village.
"""

import math

from bots.astral import run, RUN_TRAIL, bars_per_session
from bots.astral_tp_volume_scaled import participation

LONG_BARS = 78


def _trend(context, symbol, cap=3.0):
    """The parent's volume term: 16-bar times 78-bar participation."""
    return (participation(context, symbol, cap=cap)
            * participation(context, symbol, recent_bars=LONG_BARS, cap=cap))

DAILY_ATRS = 2.0


def targets(context, symbol, read, entry, basket):
    daily = read["atr_pct"] * math.sqrt(bars_per_session(symbol))
    return [(entry * (1 + DAILY_ATRS * daily * _trend(context, symbol)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
