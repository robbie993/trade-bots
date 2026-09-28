"""ASTRAL VS 10: VOLUME-SCALED VWAP BAND.

Anchored on volume rather than the entry: the three-session (78-bar)
VWAP plus 4 volume-weighted standard deviations, times participation. The
volume-weighted price is where the basket's money actually changed hands;
the band is how far price has room to stretch from it. Without volume
there is no VWAP, so a feed without it gets no target.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

import math

from bots.astral import run
from bots.astral_tp_volume_scaled import participation

BAND_BARS = 78
SIGMAS = 4.0


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
    return run(context, take_profit=targets)
