"""ASTRAL X4.10: CORRELATION 0.5.

Opens only in baskets correlated at 0.5 or more, not 0.3.

Built on astral_x3_revert_any_bar: the best profit factor of round x3 (0.80,
63% of its catch-up exits winning): reversion into the basket's laggards, no
expansion gate. Only what is described above differs from the parent. See
`run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x3_revert_any_bar import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x3_revert_any_bar import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x3_revert_any_bar import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x3_revert_any_bar import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'MIN_CORRELATION': 0.5})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
