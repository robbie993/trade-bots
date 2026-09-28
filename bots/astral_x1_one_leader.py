"""ASTRAL X1.6: ONE LEADER, DOUBLE SIZE.

Holds only the basket's top name, at 80% of equity rather than two at 40%.

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


PARAMS = dict(PARENT_PARAMS, **{'LEADERS': 1, 'LEADER_WEIGHT': 0.8})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
