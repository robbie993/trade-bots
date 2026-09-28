"""ASTRAL NX 7: SCALED BY MOMENTUM.

The parent's distance times the name's current momentum z-score (at least 2x): the stronger the move, the farther it is allowed to go.

An iteration of astral_vt_basket, still the best strategy in
scripts/astral_backtest.py. Every target here is set relative to the
parent's own target distance, so "4x" means four times as far above the
entry as the parent would sell. Same trail (RUN_TRAIL). See `run` in
bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
from bots.astral_vt_basket import targets as parent_targets


def _distance(context, symbol, read, entry, basket):
    """How far above the entry the parent puts its target, as a fraction."""
    ladder = parent_targets(context, symbol, read, entry, basket)
    return ladder[0][0] / entry - 1 if ladder else None

FLOOR = 2.0


def targets(context, symbol, read, entry, basket):
    d = _distance(context, symbol, read, entry, basket)
    return [(entry * (1 + max(FLOOR, read["z"]) * d), 0.0)] if d else []


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
