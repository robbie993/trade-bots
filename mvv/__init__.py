"""Minimum Viable Village (MVV) — Phase 1, "The Experiment Ledger".

The smallest system that can answer one question: can it consistently find,
test, market, fulfill and manage profitable products with real money after all
costs? If yes, scale. If no, kill it.

Five components, nothing else:

    mvv.scout        1. Scout                — discover products
    mvv.economics    2. Economic Calculator  — unit economics, margin, CAC, ROI
    mvv.ledger       3. Experiment Ledger    — the single source of truth
    mvv.human_gate   4. Human Approval Gate  — hard boundary for spend
    mvv.orchestrator 5. Orchestrator         — coordinate the loop

Supporting modules: kill_criteria (the thresholds), cash (working capital),
monitoring (alerts and reports), metrics (ingestion), db, models, money.
"""

from __future__ import annotations

__version__ = "1.0.0"

from .cash import CashLedger, InsufficientCash
from .config import Config, load_config
from .db import Database
from .economics import EconomicCalculator, UnitEconomics
from .human_gate import Approval, ApprovalAction, ApprovalRequired, HumanGate
from .kill_criteria import (
    DEFAULT_GATE,
    DEFAULT_THRESHOLDS,
    KillThresholds,
    SampleGate,
    meets_sample_gate,
    should_kill_experiment,
)
from .ledger import ExperimentLedger, LedgerError
from .models import CashFlowEntry, Experiment, Order, Status
from .monitoring import HealthMonitor
from .orchestrator import Orchestrator, TickReport
from .scout import ProductCandidate, Scout

__all__ = [
    "Approval",
    "ApprovalAction",
    "ApprovalRequired",
    "CashFlowEntry",
    "CashLedger",
    "Config",
    "DEFAULT_GATE",
    "DEFAULT_THRESHOLDS",
    "Database",
    "EconomicCalculator",
    "Experiment",
    "ExperimentLedger",
    "HealthMonitor",
    "HumanGate",
    "InsufficientCash",
    "KillThresholds",
    "LedgerError",
    "Order",
    "Orchestrator",
    "ProductCandidate",
    "SampleGate",
    "Scout",
    "Status",
    "TickReport",
    "UnitEconomics",
    "load_config",
    "meets_sample_gate",
    "should_kill_experiment",
]
