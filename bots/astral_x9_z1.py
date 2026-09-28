"""ASTRAL X9.1: CATCH-UP AT +1 SIGMA.

Sells at +1.0 sigma instead of +0.5.

Built on astral_x8_all_in: the best strategy so far: +$7,486 at profit
factor 1.75 on the search window, +$4,074 at 2.25 held out. It takes profit
when a laggard's move reaches +0.5 sigma; every iteration here takes it much
later. Only what is described above differs from the parent. See `run` in
bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x8_all_in import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x8_all_in import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x8_all_in import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x8_all_in import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'REVERT_EXIT_Z': 1.0})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
