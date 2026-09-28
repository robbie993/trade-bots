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


def participation(context, symbol, recent_bars=RECENT_BARS, cap=MAX_PARTICIPATION):
    """Recent average volume over the median bar, held between 1x and ``cap``.
    1.0 when the feed has no volume or not enough of it. The astral_vs_*
    iterations build on this."""
    volumes = [float(v) for v in context.volumes(symbol)]
    if len(volumes) <= recent_bars:
        return 1.0
    typical = _median(volumes)
    if typical <= 0:
        return 1.0
    recent = sum(volumes[-recent_bars:]) / recent_bars
    return min(cap, max(1.0, recent / typical))


def targets(context, symbol, read, entry, basket):
    p = participation(context, symbol)
    return [(entry * (1 + BASE_ATRS * p * read["atr_pct"]), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
