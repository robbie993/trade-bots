"""ASTRAL X9.10: VWAP PLUS TWO SIGMA.

The three-session volume-weighted price plus two volume-weighted standard
deviations: from below the group's fair price to well above it. No catch-up
exit.

Built on astral_x8_all_in: the best strategy so far: +$7,486 at profit
factor 1.75 on the search window, +$4,074 at 2.25 held out. It takes profit
when a laggard's move reaches +0.5 sigma; every iteration here takes it much
later. Only what is described above differs from the parent. See `run` in
bots/astral.py. Not in the village.
"""

import math

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


PARAMS = dict(PARENT_PARAMS, **{'REVERT_EXIT_Z': 99.0})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


def targets(context, symbol, read, entry, basket):
    closes = [float(c) for c in context.closes(symbol, 78)]
    volumes = [float(v) for v in context.volumes(symbol, 78)]
    total = sum(volumes)
    if len(closes) != len(volumes) or total <= 0:
        return []
    vwap = sum(c * v for c, v in zip(closes, volumes)) / total
    sd = math.sqrt(sum(v * (c - vwap) ** 2 for c, v in zip(closes, volumes)) / total)
    return [(vwap + 2 * sd, 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
