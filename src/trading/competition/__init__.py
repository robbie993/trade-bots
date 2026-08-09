"""Competition — tokens, titles and bouts.

    tokens.py  the token ledger: awards, penalties, titles, reputation
    arena.py   head-to-head bouts, milestones, the standings table

Tokens are points. They live in their own tables, nothing in ``brokerage/``
reads them, and no function anywhere converts a balance into capital. The
allocator stays the only route to money, because that is where the human gate
is.
"""

from __future__ import annotations

from .arena import Arena, Bout, METRICS
from .tokens import Balance, InsufficientTokens, TITLES, TokenLedger, title_for

__all__ = [
    "Arena",
    "Balance",
    "Bout",
    "InsufficientTokens",
    "METRICS",
    "TITLES",
    "TokenLedger",
    "title_for",
]
