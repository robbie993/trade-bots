"""ASTRAL X10.3: HALF AT +0.5 SIGMA, HALF AT +2.

Half at the parent's exit, the rest held until the laggard is leading.

Built on astral_x8_all_in: still the best strategy: +$7,486 (profit factor
1.75) searched, +$4,074 (2.25) held out. Round x9 showed a flat far exit
fails held out; these raise it only for part of the position, or only when
the trade says there is room. Only what is described above differs from the
parent. See `run` in bots/astral.py. Not in the village.
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
try:
    from bots.astral_x8_all_in import revert_exit as parent_revert_exit
except ImportError:
    parent_revert_exit = None
from bots.astral_x8_all_in import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def revert_exit(context, symbol, read, basket):
    return [(0.5, 0.5), (2.0, 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter, revert_exit=revert_exit)
