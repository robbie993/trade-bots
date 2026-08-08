"""The Brokerage — oversight, in one object.

The daily cycle, in the order the build document gives it, with the village's
refusals bolted on:

    reconcile → evaluate → kill checks → allocate → leaderboard

``reconcile`` comes first and is not optional. If any firm's books do not add
up, ``evaluate`` raises ``LedgerNotReconciled`` and nothing else runs. Every
subsequent step is a decision made *from* the books, so a decision made from
broken books is worse than no decision at all.

What the brokerage may do alone: pause a firm, cut an allocation, take back
uninvested cash, halt everything. What it may never do alone: raise an
allocation, or kill a firm for good. Both of those write an approval request
and stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ...money import fmt_money
from ..config import TradingConfig
from ..data.market_data import MarketData
from ..firms.kill_switch import INSUFFICIENT_DATA
from ..models import FirmRecord, FirmStatus
from ..store import TradingStore
from .allocator import Allocator
from .evaluator import Evaluator, Scorecard
from .kill_switch import BrokerageKillSwitch
from .leaderboard import Leaderboard
from .reconciliation import LedgerNotReconciled, Reconciler, ReconciliationReport


@dataclass
class OversightReport:
    reconciliation: Optional[ReconciliationReport] = None
    cards: list = field(default_factory=list)
    kills_requested: list = field(default_factory=list)
    paused: list = field(default_factory=list)
    allocation_changes: list = field(default_factory=list)
    leaderboard: Optional[Leaderboard] = None
    halted: Optional[dict] = None

    def summary(self) -> str:
        lines = [self.reconciliation.summary() if self.reconciliation else "not reconciled"]
        for card in self.cards:
            lines.append(
                f"  {card.firm_key}: score {card.score} equity {fmt_money(card.equity)} "
                f"({card.closed_trades} closed trades"
                + ("" if card.sufficient_data else ", below sample gate")
                + ")"
            )
        for paused in self.paused:
            lines.append(f"  PAUSED {paused['firm']}: {paused['reason']}")
        for change in self.allocation_changes:
            lines.append(f"  {change}")
        if self.halted:
            lines.append(f"  HALTED: {self.halted['reason']}")
        return "\n".join(lines)


class Brokerage:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
        gate=None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.gate = gate
        self.reconciler = Reconciler(store, self.config.brokerage)
        self.evaluator = Evaluator(store, self.config)
        self.allocator = Allocator(store, self.config.brokerage, gate)
        self.kill_switch = BrokerageKillSwitch(store, self.config, gate)

    # -- the gate ---------------------------------------------------------
    def reconcile(self, market: MarketData) -> ReconciliationReport:
        report = self.reconciler.check(self.store.firms(), market)
        if not report.ok:
            self.store.record_event(
                "reconcile_failed",
                report.summary(),
                payload={"breaks": [str(b) for b in report.breaks]},
            )
        return report

    def require_reconciled(self, market: MarketData) -> ReconciliationReport:
        report = self.reconcile(market)
        if not report.ok:
            raise LedgerNotReconciled(report)
        return report

    # -- the cycle --------------------------------------------------------
    def oversee(self, market: MarketData) -> OversightReport:
        """One full pass. Raises if the books do not reconcile."""
        report = OversightReport()
        report.reconciliation = self.require_reconciled(market)

        firms = self.store.firms()
        report.cards = self.evaluator.evaluate_all(firms, market)
        for card in report.cards:
            self.evaluator.persist(card)

        report.paused, report.kills_requested = self.check_kills(firms, report.cards)

        halted, reason, state = self.kill_switch.check(report.cards)
        if halted:
            report.halted = self.kill_switch.halt(reason, state)
            # A halted ecosystem does not reallocate. Capital decisions during
            # a stop are exactly the decisions a stop exists to prevent.
            report.leaderboard = Leaderboard(report.cards, self.store.firms())
            return report

        changes = self.allocator.plan(report.cards, firms)
        report.allocation_changes = self.allocator.apply(changes)
        report.leaderboard = Leaderboard(report.cards, self.store.firms())
        return report

    def check_kills(self, firms: Sequence[FirmRecord], cards: Sequence[Scorecard]):
        """Pause firms that tripped their own switch; ask before killing one."""
        by_id = {c.firm_id: c for c in cards}
        paused, requested = [], []

        for firm in firms:
            if firm.is_killed:
                continue
            card = by_id.get(firm.id)
            if card is None:
                continue
            from ..firms.kill_switch import should_kill_firm

            should, reason = should_kill_firm(card.to_metrics(), self.config.kill)
            if not should or reason == INSUFFICIENT_DATA:
                continue

            if firm.is_active:
                self.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
                self.store.record_event(
                    "kill_triggered",
                    f"{firm.firm_key}: {reason} — trading paused, kill needs approval",
                    firm_id=firm.id,
                    payload={"reason": reason, "score": str(card.score)},
                )
                paused.append({"firm": firm.firm_key, "reason": reason})

            approval = self.request_kill(firm, reason, card)
            if approval is not None:
                requested.append(approval.id)
        return paused, requested

    def request_kill(self, firm: FirmRecord, reason: str, card: Optional[Scorecard] = None):
        if self.gate is None:
            return None
        from ...agents.human_gate import ApprovalAction

        details = {
            "firm": firm.firm_key,
            "name": firm.name,
            "allocation": str(firm.allocation),
            "reason": reason,
        }
        if card is not None:
            details.update(
                {
                    "score": str(card.score),
                    "equity": str(card.equity),
                    "drawdown_pct": str(card.drawdown_pct),
                    "win_rate_pct": str(card.win_rate_pct),
                    "closed_trades": card.closed_trades,
                }
            )
        return self.gate.request(
            ApprovalAction.KILL_FIRM.value,
            f"Kill firm {firm.firm_key} ({firm.name}): {reason}",
            details=details,
            dedupe_key=f"kill:{firm.firm_key}",
        )

    # -- approved outcomes -------------------------------------------------
    def kill_firm(self, firm_key: str, reason: str = "approved by human") -> dict:
        """Carry out an approved kill: close the firm and reclaim its cash.

        Open positions are *not* liquidated here. Selling is the firm's job
        and goes through the venue like any other trade; a killed firm keeps
        exiting on the next tick and hands back cash as it does.
        """
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        self.store.set_firm_status(firm.id, FirmStatus.KILLED.value, reason)
        released = self.allocator.release(
            self.store.require_firm_by_id(firm.id), f"firm killed: {reason}"
        )
        self.store.record_event(
            "kill",
            f"{firm_key} killed: {reason}",
            firm_id=firm.id,
            payload={"reason": reason, "returned": str(abs(released.delta))},
        )
        return {"firm": firm_key, "reason": reason, "returned": str(abs(released.delta))}

    def resume_firm(self, firm_key: str, by: str) -> dict:
        """Un-pause a firm. Only ever called from an explicit human command."""
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        if firm.is_killed:
            raise ValueError(f"{firm_key} is killed; killed firms do not resume")
        self.store.set_firm_status(firm.id, FirmStatus.ACTIVE.value)
        self.store.record_event(
            "resume", f"{firm_key} resumed by {by}", firm_id=firm.id, payload={"by": by}
        )
        return {"firm": firm_key, "status": FirmStatus.ACTIVE.value, "by": by}

    def leaderboard(self, market: MarketData) -> Leaderboard:
        firms = self.store.firms()
        cards = self.evaluator.evaluate_all(firms, market)
        return Leaderboard(cards, firms)


__all__ = ["Brokerage", "OversightReport"]
