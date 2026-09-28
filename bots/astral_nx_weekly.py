"""ASTRAL NX 10: AT LEAST A WEEK'S MOVE.

The farther of the parent's target and 2 weekly expected moves, a weekly move being bar volatility scaled by the square root of five sessions of bars. A swing-trade target on an intraday entry.

An iteration of astral_vt_basket, still the best strategy in
scripts/astral_backtest.py. Every target here is set relative to the
parent's own target distance, so "4x" means four times as far above the
entry as the parent would sell. Same trail (RUN_TRAIL). See `run` in
bots/astral.py. Not in the village.
"""

import math

from bots.astral import RUN_TRAIL, run
from bots.astral import bars_per_session
from bots.astral_vt_basket import targets as parent_targets


def _distance(context, symbol, read, entry, basket):
    """How far above the entry the parent puts its target, as a fraction."""
    ladder = parent_targets(context, symbol, read, entry, basket)
    return ladder[0][0] / entry - 1 if ladder else None

WEEKS = 2.0


def targets(context, symbol, read, entry, basket):
    d = _distance(context, symbol, read, entry, basket)
    weekly = read["slow_vol"] * math.sqrt(5 * bars_per_session(symbol))
    return [(entry * (1 + max(d or 0.0, WEEKS * weekly)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
