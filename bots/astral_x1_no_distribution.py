"""ASTRAL X1.3: NO DISTRIBUTION EXIT.

No trimming on heavy volume into a down bar. In the parent that exit closed
about 140 trades and every one lost.

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


PARAMS = dict(PARENT_PARAMS, **{'DISTRIBUTION': False})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
