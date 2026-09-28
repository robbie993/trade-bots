"""ASTRAL VS 3: VOLUME-SCALED OVER A WHOLE SESSION.

Participation is measured over the last 26 bars (a full equity session)
instead of 16, with the cap raised from 3x to 4x, and the base raised to
10 ATR: 10 to 40 ATR. A day of heavy trading is a stronger claim than four
hours of it, so it is allowed to move the target further.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 10.0
SESSION_BARS = 26
CAP = 4.0


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol, recent_bars=SESSION_BARS, cap=CAP)
    return [(entry * (1 + BASE_ATRS * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
