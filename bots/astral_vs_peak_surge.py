"""ASTRAL VS 5: VOLUME-SCALED ON THE PEAK SURGE.

The parent averages the last 16 bars. This uses the single heaviest bar
among them against a typical bar, capped at 5x, with an 8 ATR base: 8 to
40 ATR. One climactic bar inside the leg marks the move as big.

An iteration of astral_tp_volume_scaled, the best of the first ten take-
profit iterations in scripts/astral_backtest.py. Everything else is Astral,
unchanged: see `run` in bots/astral.py. Not in the village.
"""

from bots.astral import run
from bots.astral_tp_volume_scaled import _median

BASE_ATRS = 8.0
RECENT_BARS = 16
CAP = 5.0


def targets(context, symbol, read, entry, basket):
    volumes = [float(v) for v in context.volumes(symbol)]
    surge = 1.0
    if len(volumes) > RECENT_BARS:
        typical = _median(volumes)
        if typical > 0:
            surge = min(CAP, max(1.0, max(volumes[-RECENT_BARS:]) / typical))
    return [(entry * (1 + BASE_ATRS * surge * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
