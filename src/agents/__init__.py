"""The five core components — spec section 2.

    1. Scout                 scout.py        discover products
    2. Economic Calculator   economic_calculator.py
    3. Experiment Ledger     experiment_ledger.py
    4. Human Approval Gate   human_gate.py
    5. Orchestrator          orchestrator.py

Plus the supporting pieces the spec's own sections require: cash_ledger.py
(section 7), monitoring.py (sections 8 and 14), metrics.py (ingestion for
section 5.4).
"""

from __future__ import annotations

from .cash_ledger import CashLedger, InsufficientCash
from .economic_calculator import EconomicCalculator, UnitEconomics
from .experiment_ledger import ExperimentLedger, LedgerError
from .human_gate import Approval, ApprovalAction, ApprovalRequired, HumanGate
from .metrics import MetricsSnapshot
from .monitoring import HealthMonitor
from .orchestrator import Orchestrator, TickReport
from .scout import ProductCandidate, Scout, SourceNotConfigured, build_scout

__all__ = [
    "Approval",
    "ApprovalAction",
    "ApprovalRequired",
    "CashLedger",
    "EconomicCalculator",
    "ExperimentLedger",
    "HealthMonitor",
    "HumanGate",
    "InsufficientCash",
    "LedgerError",
    "MetricsSnapshot",
    "Orchestrator",
    "ProductCandidate",
    "Scout",
    "SourceNotConfigured",
    "TickReport",
    "UnitEconomics",
    "build_scout",
]
