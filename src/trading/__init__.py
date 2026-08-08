"""The AI Village, Trading Edition — a multi-firm trading ecosystem.

Same village, different goods. The physical village (``src/agents``) tests
products; this one tests strategies. The invariants are identical and are the
reason this package exists inside the same repository rather than beside it:

    The system may always stop the bleeding, and may never start it.

Concretely, in this package:

* A firm may always close a position, pause itself, or hand capital back.
  None of that needs a human.
* A firm may never open a position on a live venue, and the brokerage may
  never *raise* an allocation, without an approved row in ``human_approvals``.
* Nothing is published, allocated or evolved until the ledger reconciles.
  ``brokerage.reconciliation`` is the gate every action passes through.
* Every monetary value is a ``decimal.Decimal`` (``src/money.py``). Prices are
  Decimal too — a fill price that drifts in binary is a fill you cannot audit.

Layout mirrors the build document:

    data/        market data feeds                (deterministic by default)
    firms/       analysts, bull/bear, trader, risk manager, kill switch
    brokerage/   evaluate, allocate, leaderboard, reconcile, kill
    brain/       memory + evolution over strategy genomes
    heart/       conscience (six moral foundations) + compliance
    gateway/     LLM routing, with an offline deterministic fallback
    execution/   paper venue (default) and live venue adapters
    audit/       Obsidian-shaped markdown trail
    ecosystem.py the loop that ties them together
    adapters.py  optional bridges to the external frameworks
"""

from __future__ import annotations

from .config import TradingConfig, load_trading_config
from .models import Bar, Fill, Position, Side, Signal, TradeProposal

__all__ = [
    "Bar",
    "Fill",
    "Position",
    "Side",
    "Signal",
    "TradeProposal",
    "TradingConfig",
    "load_trading_config",
]
