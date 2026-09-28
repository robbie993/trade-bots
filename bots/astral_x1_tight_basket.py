"""ASTRAL X1.7: TIGHT BASKETS ONLY.

Opens only when the basket's average correlation is at least 0.6 (not 0.3):
a rotation needs a group that is really moving as one.

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


PARAMS = dict(PARENT_PARAMS, **{'MIN_CORRELATION': 0.6})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
