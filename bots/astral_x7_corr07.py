"""ASTRAL X7.7: CORRELATION 0.7.

Tighter baskets again.

Built on astral_x6_stop6: the best of round x6 (+$2,957, profit factor
1.36): the x5 reversion with a 6 ATR stop. Only what is described above
differs from the parent. See `run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x6_stop6 import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x6_stop6 import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x6_stop6 import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x6_stop6 import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'MIN_CORRELATION': 0.7})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
