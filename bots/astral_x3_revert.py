"""ASTRAL X3.1: REVERSION: BUY THE LAGGARD.

Turns the rotation around. Buys the basket's two weakest names (momentum z
of -0.5 or lower) on an up bar with heavy volume, in a basket still
correlated at 0.3, and sells once the name's move is no longer negative.
Momentum entries lost in every setting tried; in a correlated basket the
laggard catching up is the more usual trade.

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


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
