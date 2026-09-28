"""ASTRAL VS 9: VOLUME-SCALED, SHORT AND LONG.

Two participations multiplied: the last 16 bars and the last three
sessions (78 bars), each held between 1x and 3x, on an 8 ATR base: 8 to
72 ATR. A burst on top of volume that has been building for days is the
strongest volume signal Astral can read, and gets the farthest target.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BASE_ATRS = 8.0
LONG_BARS = 78


def targets(context, symbol, read, entry, basket):
    short = participation(context, symbol)
    long = participation(context, symbol, recent_bars=LONG_BARS)
    return [(entry * (1 + BASE_ATRS * short * long * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
