"""The Allocator — capital to winners, away from losers.

The one asymmetry that makes this layer safe:

    Cutting an allocation happens immediately and by itself.
    Raising an allocation writes an approval request and stops.

That is the village's core principle applied to capital. Taking money off a
losing strategy is stopping the bleeding; handing more money to a winning one
is starting it, and no score is allowed to do that on its own.

Two more refusals worth stating plainly, both of which exist because a score
computed from four trades is not evidence:

* A firm below the sample gate is left alone entirely. It is neither promoted
  nor cut — it is still being measured.
* Total deployed capital may never exceed ``brokerage.total_capital``. If the
  winners' raises would breach it, the raises are trimmed to what is left,
  not the cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from ...money import D, ZERO, fmt_money, money
from ..config import BrokerageConfig
from ..models import FirmRecord
from ..store import TradingStore
from .evaluator import Scorecard


@dataclass
class AllocationChange:
    firm_key: str
    firm_id: Optional[int]
    old_allocation: Decimal
    new_allocation: Decimal
    reason: str
    applied: bool = False
    approval_id: Optional[int] = None

    @property
    def delta(self) -> Decimal:
        return money(self.new_allocation - self.old_allocation)

    @property
    def is_increase(self) -> bool:
        return self.delta > 0

    def __str__(self) -> str:
        direction = "+" if self.delta > 0 else ""
        state = "applied" if self.applied else (
            f"awaiting approval #{self.approval_id}" if self.approval_id else "not applied"
        )
        return (
            f"{self.firm_key}: {fmt_money(self.old_allocation)} -> "
            f"{fmt_money(self.new_allocation)} ({direction}{fmt_money(self.delta)}) "
            f"[{state}] {self.reason}"
        )


class Allocator:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[BrokerageConfig] = None,
        gate=None,
    ):
        self.store = store
        self.config = config or BrokerageConfig()
        self.gate = gate  # HumanGate, or None when increases are disabled

    def plan(self, cards: Sequence[Scorecard], firms: Sequence[FirmRecord]) -> list:
        """Work out every change without touching anything."""
        by_id = {f.id: f for f in firms}
        deployed = sum((D(f.allocation) for f in firms if not f.is_killed), ZERO)
        headroom = money(D(self.config.total_capital) - deployed)
        changes: list = []

        for card in cards:
            firm = by_id.get(card.firm_id)
            if firm is None or firm.is_killed:
                continue
            current = D(firm.allocation)

            if not card.sufficient_data:
                continue  # still being measured; capital does not move

            if card.score >= self.config.good_score:
                ceiling = money(D(firm.initial_allocation) * self.config.max_allocation_multiple)
                target = money(current * self.config.increase_step)
                target = min(target, ceiling)
                if headroom <= 0:
                    continue
                target = min(target, money(current + headroom))
                if target <= current:
                    continue
                headroom = money(headroom - (target - current))
                changes.append(
                    AllocationChange(
                        firm.firm_key,
                        firm.id,
                        current,
                        target,
                        f"score {card.score} at or above {self.config.good_score}",
                    )
                )
            elif card.score <= self.config.poor_score:
                target = max(
                    money(current * self.config.decrease_step), D(self.config.min_allocation)
                )
                if target >= current:
                    continue
                # Only uninvested cash can be withdrawn. A firm whose money is
                # in positions has its cut trimmed to the cash on hand and the
                # rest taken on a later pass, as its positions close. Taking
                # more would push cash negative, which would then block the
                # very sells that free the money.
                #
                # Note `withdrawable`, not `available`: the reserve floor is a
                # fraction of the allocation, so returning capital lowers the
                # floor by the same step and cannot breach it. The risk manager
                # is the one that may not touch the reserve.
                purse = self.store.cash_view(firm)
                withdrawable = money(min(current - target, purse.withdrawable))
                if withdrawable <= 0:
                    self.store.record_event(
                        "allocation_deferred",
                        f"{firm.firm_key}: cut to {fmt_money(target)} deferred — "
                        f"no uninvested cash to withdraw",
                        firm_id=firm.id,
                        payload={"target": str(target), "cash": str(firm.cash)},
                    )
                    continue
                changes.append(
                    AllocationChange(
                        firm.firm_key,
                        firm.id,
                        current,
                        money(current - withdrawable),
                        f"score {card.score} at or below {self.config.poor_score}"
                        + (
                            f" (cut limited to {fmt_money(withdrawable)} of uninvested cash)"
                            if withdrawable < current - target
                            else ""
                        ),
                    )
                )
        return changes

    def apply(self, changes: Sequence[AllocationChange]) -> list:
        """Apply cuts. Raises are requested and left for a human."""
        for change in changes:
            if change.is_increase and self.config.allocation_increase_needs_approval:
                approval = self._request_increase(change)
                change.approval_id = getattr(approval, "id", None)
                change.applied = False
                self.store.record_event(
                    "allocation_requested",
                    f"{change.firm_key}: increase to {fmt_money(change.new_allocation)} "
                    "awaiting approval",
                    firm_id=change.firm_id,
                    payload={
                        "old": str(change.old_allocation),
                        "new": str(change.new_allocation),
                        "reason": change.reason,
                        "approval_id": change.approval_id,
                    },
                )
                continue

            self.store.set_allocation(change.firm_id, change.new_allocation, change.delta)
            change.applied = True
            self.store.record_event(
                "allocation",
                str(change),
                firm_id=change.firm_id,
                payload={
                    "old": str(change.old_allocation),
                    "new": str(change.new_allocation),
                    "reason": change.reason,
                },
            )
        return list(changes)

    def apply_approved_increase(self, firm_key: str, new_allocation: Decimal) -> AllocationChange:
        """Called once a human has approved a raise. The only way capital grows."""
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        change = AllocationChange(
            firm_key, firm.id, D(firm.allocation), money(new_allocation), "approved by human"
        )
        self.store.set_allocation(firm.id, change.new_allocation, change.delta)
        change.applied = True
        self.store.record_event(
            "allocation",
            str(change),
            firm_id=firm.id,
            payload={"old": str(change.old_allocation), "new": str(change.new_allocation)},
        )
        return change

    def release(self, firm: FirmRecord, reason: str) -> AllocationChange:
        """Take a dead firm's uninvested cash back. Never needs approval.

        ``allocation`` is net capital *entrusted*, so returning more than was
        put in leaves it negative — that is a firm which handed back its stake
        plus a profit, and the number is the honest one. It is not clamped,
        because the reconciler's identity (cash = allocation + Σ cash_delta)
        is what makes every other figure trustworthy, and clamping would
        silently break it. ``trade allocations`` renders it as capital
        returned rather than as a negative allocation.
        """
        returned = money(firm.cash)
        change = AllocationChange(
            firm.firm_key,
            firm.id,
            D(firm.allocation),
            money(D(firm.allocation) - returned),
            reason,
        )
        self.store.set_allocation(firm.id, change.new_allocation, -returned)
        change.applied = True
        self.store.record_event(
            "allocation_released",
            f"{firm.firm_key}: returned {fmt_money(returned)} to the brokerage ({reason})",
            firm_id=firm.id,
            payload={"returned": str(returned), "reason": reason},
        )
        return change

    def _request_increase(self, change: AllocationChange):
        if self.gate is None:
            return None
        from ...agents.human_gate import ApprovalAction

        return self.gate.request(
            ApprovalAction.ALLOCATE_CAPITAL.value,
            f"Raise {change.firm_key}'s trading allocation from "
            f"{fmt_money(change.old_allocation)} to {fmt_money(change.new_allocation)} "
            f"({change.reason})",
            amount=change.delta,
            details={
                "kind": "trading_allocation",
                "firm": change.firm_key,
                "old_allocation": str(change.old_allocation),
                "new_allocation": str(change.new_allocation),
                "reason": change.reason,
            },
            dedupe_key=f"allocation:{change.firm_key}",
        )


__all__ = ["AllocationChange", "Allocator"]
