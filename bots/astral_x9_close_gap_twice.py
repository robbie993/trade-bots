"""ASTRAL X9.8: CLOSE THE GAP TO THE LEADER, TWICE.

The gap is how far this name's last 16 bars trail the basket's best name's.
The target is twice that gap above the entry: catch the leader and pass it.
No catch-up exit.

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


PARAMS = dict(PARENT_PARAMS, **{'REVERT_EXIT_Z': 99.0})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


def targets(context, symbol, read, entry, basket):
    closes = {s: [float(c) for c in context.closes(s, 17)] for s in basket}
    moves = {s: c[-1] / c[0] - 1 for s, c in closes.items() if len(c) == 17 and c[0] > 0}
    if symbol not in moves or len(moves) < 2:
        return [(entry, 0.0)]  # lifted to the 2R floor
    gap = max(0.0, max(moves.values()) - moves[symbol])
    return [(entry * (1 + 2 * gap), 0.0)]  # no gap: lifted to the 2R floor


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
