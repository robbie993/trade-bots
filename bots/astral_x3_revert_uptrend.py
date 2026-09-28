"""ASTRAL X3.4: REVERSION, DIPS IN AN UPTREND.

As revert, but only in names above a rising three-session average: a dip in
an uptrend rather than a name breaking down.

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


PARAMS = dict(PARENT_PARAMS, **{'MODE': 'reversion'})
TRAIL = PARENT_TRAIL


def entry_filter(context, symbol, read, basket):
    if parent_filter is not None and not parent_filter(context, symbol, read, basket):
        return False
    closes = [float(c) for c in context.closes(symbol, 105)]
    if len(closes) < 105:
        return False
    now = sum(closes[-78:]) / 78
    before = sum(closes[-104:-26]) / 78
    return closes[-1] > now > before


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
