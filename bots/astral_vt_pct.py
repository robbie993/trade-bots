"""ASTRAL VT 8: A PERCENT.

10% times short x long participation for equities (10 to 90%), 20% for crypto (20 to 180%). Sized in plain percent rather than volatility.

An iteration of astral_run_volume_trend (8 to 72 ATR), the best of the
astral_run_* round in scripts/astral_backtest.py, with its target pushed
further out. Same trail as the parent (RUN_TRAIL: 4 ATR off the last
session's high), so only the target differs. See `run` in
bots/astral.py. Not in the village.
"""

from bots.astral import run, RUN_TRAIL
from bots.astral_tp_volume_scaled import participation

LONG_BARS = 78


def _trend(context, symbol, cap=3.0):
    """The parent's volume term: 16-bar times 78-bar participation."""
    return (participation(context, symbol, cap=cap)
            * participation(context, symbol, recent_bars=LONG_BARS, cap=cap))

EQUITY_PCT = 0.10
CRYPTO_PCT = 0.20


def targets(context, symbol, read, entry, basket):
    pct = CRYPTO_PCT if "-" in str(symbol) else EQUITY_PCT
    return [(entry * (1 + pct * _trend(context, symbol)), 0.0)]


def propose(context):
    return run(context, take_profit=targets, trail=RUN_TRAIL)
