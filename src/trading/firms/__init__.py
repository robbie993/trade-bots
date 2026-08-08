"""The trading firms — multi-strategy pods.

    analysts.py      fundamental / technical / sentiment / macro / on-chain
    researchers.py   the bull-versus-bear debate
    trader.py        conviction to position size
    risk_manager.py  the firm's own brake
    kill_switch.py   the firm's own stop
    firm.py          the pod that runs them in order
    spec.py          config/firm_config.yaml -> FirmSpec
"""

from __future__ import annotations

from .analysts import ANALYSTS, build_analysts
from .firm import Firm
from .kill_switch import FirmMetrics, KillSwitch, kill_check_table, should_kill_firm
from .researchers import DebateResult, DebateRoom
from .risk_manager import RiskDecision, RiskManager
from .spec import FirmSpec, FirmSpecError, load_firm_specs
from .trader import Trader

__all__ = [
    "ANALYSTS",
    "DebateResult",
    "DebateRoom",
    "Firm",
    "FirmMetrics",
    "FirmSpec",
    "FirmSpecError",
    "KillSwitch",
    "RiskDecision",
    "RiskManager",
    "Trader",
    "build_analysts",
    "kill_check_table",
    "load_firm_specs",
    "should_kill_firm",
]
