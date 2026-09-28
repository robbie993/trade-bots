"""ASTRAL X1.1: NO CRYPTO.

Never opens a crypto position. The crypto basket lost 19% in the parent,
about what it paid in fees: 25 bps a side is more than a 15m rotation
clears.

Built on astral_vt_basket: the best strategy after six take-profit rounds,
which only moved the exit. Only what is described above differs from the
parent. See `run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_vt_basket import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_vt_basket import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_vt_basket import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_vt_basket import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{})
TRAIL = PARENT_TRAIL


def entry_filter(context, symbol, read, basket):
    if parent_filter is not None and not parent_filter(context, symbol, read, basket):
        return False
    return "-" not in str(symbol)


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
