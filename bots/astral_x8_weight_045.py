"""ASTRAL X8.2: 45% A NAME.

Three laggards at 45% each. Cash caps the total, so the third is funded only
when the first two are not full.

Built on astral_x7_corr07: the pick of the search rounds: +$2,723 at profit
factor 1.69 on 2026-07-06..09-07 and +$1,693 at 2.44 on the held-out
09-08..09-28. Only what is described above differs from the parent. See
`run` in bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x7_corr07 import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x7_corr07 import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x7_corr07 import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x7_corr07 import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'LEADER_WEIGHT': 0.45})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
