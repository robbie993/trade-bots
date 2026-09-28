"""ASTRAL X5.10: LIGHTEST VOLUME GATE.

A tranche at 0.5x relative volume, two at 1.5x: lighter still, since
lightening it was round x4's biggest gain.

Built on astral_x4_lighter_volume: the best of round x4 (profit factor
0.98): laggard reversion with a lighter volume gate (1.0x a tranche, 2.0x
for two). Only what is described above differs from the parent. See `run` in
bots/astral.py. Not in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x4_lighter_volume import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x4_lighter_volume import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x4_lighter_volume import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x4_lighter_volume import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'RVOL_ADD': 0.5, 'RVOL_SURGE': 1.5})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
