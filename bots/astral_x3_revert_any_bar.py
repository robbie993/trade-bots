"""ASTRAL X3.5: REVERSION WITHOUT THE EXPANSION GATE.

As revert, but volatility expansion is not required to open. A laggard
drifting quietly behind its group is still a laggard.

Built on astral_x2_no_distribution: the best profit factor of round x2 on
the search window: no crypto, no distribution trims. Only what is described
above differs from the parent. See `run` in bots/astral.py. Not in the
village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x2_no_distribution import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x2_no_distribution import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x2_no_distribution import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x2_no_distribution import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'MODE': 'reversion', 'MIN_VOL_EXPANSION': 0.0})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
