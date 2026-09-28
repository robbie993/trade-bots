"""ASTRAL TP 7: VOLUME-SCALED TARGET.

The same thing that sizes the entries sets the exit. Participation is the
last 16 bars' average volume over the median bar in the held history,
capped between 1x and 3x. The target is 6 ATR times that: 6 ATR on an
ordinary tape, up to 18 ATR when the move is being traded heavily. Heavy
volume usually means a move with further to go.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

BASE_ATRS = 6.0
RECENT_BARS = 16
MAX_PARTICIPATION = 3.0


def _median(xs):
    ordered = sorted(xs)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def targets(context, symbol, read, entry, basket):
    volumes = [float(v) for v in context.volumes(symbol)]
    participation = 1.0
    if len(volumes) > RECENT_BARS:
        typical = _median(volumes)
        if typical > 0:
            recent = sum(volumes[-RECENT_BARS:]) / RECENT_BARS
            participation = min(MAX_PARTICIPATION, max(1.0, recent / typical))
    return [(entry * (1 + BASE_ATRS * participation * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
