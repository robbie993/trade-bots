"""ASTRAL RUN 9: VWAP BAND.

The three-session (78-bar) VWAP plus 6 volume-weighted standard deviations times participation. Anchored where the money changed hands rather than at the entry. A feed without volume has no VWAP and gets no target.

An iteration of astral_tp_volume_scaled (6 to 18 ATR), the best take-
profit in scripts/astral_backtest.py, with its target pushed far out.
Unlike the astral_vs_* round, a winner here is not closed when momentum
turns: it runs until the target or RUN_TRAIL, 4 ATR off the last
session's high. Losers keep every exit. See `run` in bots/astral.py.
Not in the village.
"""

import math

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

BAND_BARS = 78
SIGMAS = 6.0


def targets(context, symbol, read, entry, basket):
    closes = [float(c) for c in context.closes(symbol, BAND_BARS)]
    volumes = [float(v) for v in context.volumes(symbol, BAND_BARS)]
    total = sum(volumes)
    if len(closes) != len(volumes) or total <= 0:
        return []
    vwap = sum(c * v for c, v in zip(closes, volumes)) / total
    sd = math.sqrt(sum(v * (c - vwap) ** 2 for c, v in zip(closes, volumes)) / total)
    return [(vwap + SIGMAS * sd * participation(context, symbol), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
