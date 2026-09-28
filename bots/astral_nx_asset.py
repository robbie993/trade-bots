"""ASTRAL NX 9: BY ASSET CLASS.

Three times the parent's distance for equities, six times for crypto, which moves further and has to cover far higher fees.

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

def targets(context, symbol, read, entry, basket):
    d = _distance(context, symbol, read, entry, basket)
    if not d:
        return []
    return [(entry * (1 + (6.0 if "-" in str(symbol) else 3.0) * d), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
