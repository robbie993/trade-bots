"""The brokerage-wide Kill Switch — the ecosystem's KILL_ALL.

The firm-level switch (``firms.kill_switch``) asks whether *this strategy*
should stop. This one asks whether *the experiment* should stop: the question
section 14 of the original spec asks about the village as a whole.

Four independent conditions, any one of which ends the run. They mirror the
product village's stop conditions with the nouns changed:

1. Every firm is dead. There is nothing left to allocate to.
2. Total equity has fallen more than the ecosystem drawdown limit.
3. A firm kill has been sitting unanswered past the timeout — an unanswered
   kill is the gate failing, not the operator being busy.
4. Fees have eaten more than the ecosystem earned across the window, which is
   the trading version of "ad costs exceeded revenue".

Firing this pauses every firm — autonomous, immediate, no permission needed —
and requests a KILL_ALL approval. Pausing is stopping the bleeding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from ...db.connection import to_datetime, utcnow
from ...money import D, ZERO, fmt_money, money, percent
from ..config import BrokerageConfig, TradingConfig
from ..models import FirmStatus
from ..store import TradingStore


@dataclass
class EcosystemState:
    firms: int = 0
    active: int = 0
    killed: int = 0
    equity: Decimal = ZERO
    capital: Decimal = ZERO
    fees: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    @property
    def drawdown_pct(self) -> Decimal:
        if self.capital <= 0:
            return ZERO
        return percent(max(ZERO, (self.capital - self.equity) / self.capital * D(100)))


class BrokerageKillSwitch:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
        gate=None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.gate = gate

    @property
    def brokerage_config(self) -> BrokerageConfig:
        return self.config.brokerage

    def state(self, cards: Sequence) -> EcosystemState:
        firms = self.store.firms()
        state = EcosystemState(
            firms=len(firms),
            active=sum(1 for f in firms if f.is_active),
            killed=sum(1 for f in firms if f.is_killed),
            capital=money(sum((D(f.allocation) for f in firms), ZERO)),
        )
        state.equity = money(sum((D(c.equity) for c in cards), ZERO))
        state.fees = money(sum((D(c.fees) for c in cards), ZERO))
        state.realized_pnl = money(sum((D(c.realized_pnl) for c in cards), ZERO))
        return state

    def check(self, cards: Sequence) -> Tuple[bool, Optional[str], EcosystemState]:
        state = self.state(cards)
        cfg = self.brokerage_config

        if state.firms and state.killed == state.firms:
            return True, f"All {state.firms} firms have been killed", state

        if state.capital > 0 and state.drawdown_pct > cfg.ecosystem_max_drawdown_pct:
            return (
                True,
                f"Ecosystem drawdown {state.drawdown_pct}% exceeds "
                f"{cfg.ecosystem_max_drawdown_pct}%",
                state,
            )

        stale = self.unanswered_kill()
        if stale is not None:
            return True, stale, state

        if state.fees > 0 and state.realized_pnl < 0:
            burn = abs(state.realized_pnl) + state.fees
            if state.capital > 0 and burn > state.capital * D("0.10"):
                return (
                    True,
                    f"Losses plus fees of {fmt_money(burn)} exceed 10% of deployed capital "
                    f"({fmt_money(state.capital)})",
                    state,
                )

        return False, None, state

    def unanswered_kill(self) -> Optional[str]:
        """A firm kill nobody answered inside the timeout is itself a stop."""
        if self.gate is None:
            return None
        from ...agents.human_gate import ApprovalAction

        cutoff = utcnow() - timedelta(hours=self.config_timeout_hours)
        for approval in self.gate.pending():
            if approval.action != ApprovalAction.KILL_FIRM.value:
                continue
            requested = to_datetime(approval.requested_at)
            if requested is not None and requested < cutoff:
                return (
                    f"Firm kill #{approval.id} has been unanswered for over "
                    f"{self.config_timeout_hours}h"
                )
        return None

    @property
    def config_timeout_hours(self) -> int:
        # Reuses the product village's stop timeout: an unanswered kill means
        # the same thing whichever kind of experiment it was about.
        from ...config import StopConfig

        return StopConfig().kill_approval_timeout_hours

    def halt(self, reason: str, state: EcosystemState) -> dict:
        """Pause every firm and ask for KILL_ALL. Pausing needs no permission."""
        paused = []
        for firm in self.store.firms():
            if firm.is_active:
                self.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
                paused.append(firm.firm_key)
        self.store.record_event(
            "halt",
            reason,
            payload={
                "paused": paused,
                "equity": str(state.equity),
                "capital": str(state.capital),
                "drawdown_pct": str(state.drawdown_pct),
            },
        )
        approval_id = None
        if self.gate is not None:
            approval = self.gate.request_kill_all(
                f"TRADING KILL_ALL: {reason}",
                details={
                    "layer": "trading",
                    "paused_firms": paused,
                    "equity": str(state.equity),
                    "capital": str(state.capital),
                },
            )
            approval_id = approval.id
        return {
            "status": "halted",
            "reason": reason,
            "paused": paused,
            "approval_id": approval_id,
        }


__all__ = ["BrokerageKillSwitch", "EcosystemState"]
