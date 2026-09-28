"""ASTRAL X6.9: HOLD TO +1 SIGMA, STOP 6 ATR.

More room both ways.

Built on astral_x5_all_four: the first profitable version on the search
window (+$2,250, profit factor 1.24): laggard reversion, stop 5 ATR, three
laggards, correlation 0.5, held to +0.5 sigma. Only what is described above
differs from the parent. See `run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x5_all_four import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x5_all_four import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x5_all_four import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x5_all_four import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'REVERT_EXIT_Z': 1.0, 'STOP_ATRS': 6.0})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
