"""ASTRAL TP 5: THE BASKET'S BEST RUN.

The names in a basket move together, so the biggest three-session (78-bar)
run any of them has made in the held history is a run this group has shown
it can make. The leader's target is to match it from its own entry.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

RUN_BARS = 78


def _best_run(closes):
    best = 0.0
    for i, close in enumerate(closes):
        low = min(closes[max(0, i - RUN_BARS):i + 1])
        if low > 0:
            best = max(best, close / low - 1)
    return best


def targets(context, symbol, read, entry, basket):
    runs = [_best_run([float(c) for c in context.closes(s)]) for s in basket]
    best = max(runs) if runs else 0.0
    return [(entry * (1 + best), 0.0)] if best > 0 else []


def propose(context):
    return run(context, take_profit=targets)
