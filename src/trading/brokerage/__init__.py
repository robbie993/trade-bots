"""The Brokerage — oversight and allocation.

    reconciliation.py  the gate: the ledger must add up first
    evaluator.py       deterministic scoring of every firm
    allocator.py       capital to winners, away from losers
    leaderboard.py     the ranking, with its evidence
    kill_switch.py     the ecosystem-wide KILL_ALL
    brokerage.py       the daily cycle that runs them in order
"""

from __future__ import annotations

from .allocator import AllocationChange, Allocator
from .brokerage import Brokerage, OversightReport
from .evaluator import Evaluator, Scorecard
from .kill_switch import BrokerageKillSwitch, EcosystemState
from .leaderboard import Leaderboard, LeaderboardRow
from .reconciliation import (
    Break,
    LedgerNotReconciled,
    ReconciliationReport,
    Reconciler,
)

__all__ = [
    "AllocationChange",
    "Allocator",
    "Break",
    "Brokerage",
    "BrokerageKillSwitch",
    "EcosystemState",
    "Evaluator",
    "LedgerNotReconciled",
    "Leaderboard",
    "LeaderboardRow",
    "OversightReport",
    "Reconciler",
    "ReconciliationReport",
    "Scorecard",
]
