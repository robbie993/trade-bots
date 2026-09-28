"""ASTRAL X2.6: STRICTER ENTRY IN AN UPTREND.

Both of the round-x1 entry fixes that each cut the loss: z 1.5 and expansion
1.3x, and only above a rising three-session average.

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


PARAMS = dict(PARENT_PARAMS, **{'MIN_ENTRY_Z': 1.5, 'MIN_VOL_EXPANSION': 1.3})
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
