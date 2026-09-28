"""ASTRAL X2.9: TIGHTER STOP.

The stop moves from 3 to 1.5 ATR. Almost every loser in round x1 was closed
by the momentum exit at a small loss; this cuts them sooner.

Built on astral_x1_no_crypto: the best of round x1 on the search window
(2026-07-06 to 2026-09-07): vt_basket without the crypto basket. Only what
is described above differs from the parent. See `run` in bots/astral.py. Not
in the village.
"""

from bots.astral import RUN_TRAIL, run
try:
    from bots.astral_x1_no_crypto import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {}
try:
    from bots.astral_x1_no_crypto import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.astral_x1_no_crypto import entry_filter as parent_filter
except ImportError:
    parent_filter = None
from bots.astral_x1_no_crypto import targets as parent_targets


PARAMS = dict(PARENT_PARAMS, **{'STOP_ATRS': 1.5})
TRAIL = PARENT_TRAIL


entry_filter = parent_filter


targets = parent_targets


def propose(context):
    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,
               entry_filter=entry_filter)
