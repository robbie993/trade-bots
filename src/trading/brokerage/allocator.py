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

**And two about *when*, which is what went wrong here.**

A 10% cut is a judgement about a firm's quarter. It was being applied once per
*tick*, and the tick runs as often as the loop runs — so ``firm_a_etf`` took
thirteen cuts in four seconds and came out holding 25.4% of its capital
(``0.9^13 = 0.254``). Nothing about the firm changed in those four seconds; the
village simply asked the same question thirteen times and charged for each
answer. Capital now moves at most once per market bar, and the guard is read
from the event log rather than held in memory, because the loop is restarted
routinely and a guard that resets on restart is a guard that scales with how
often you deploy.

Worse, every one of those thirteen cuts landed on a firm the kill switch had
already **paused** two seconds earlier. A paused firm is not trading, so its
score cannot move, so it qualifies for a cut on every bar forever — a ratchet
with no exit, punishing a firm for a number it has been forbidden from
changing. Cuts now apply only to firms that are actually trading. Reclaiming a
stopped firm's capital is still available and still correct, but it is
``release()`` — one deliberate act with a reason on it, not a decay.
"""

from __future__ import annotations

import json
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
    #: The market bar this judgement was made on, carried into the event
    #: payload so the cadence guard reads back the bar rather than inferring
    #: one from when the row happened to be written.
    bar: str = ""

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
        resolution=None,
    ):
        self.store = store
        self.config = config or BrokerageConfig()
        self.gate = gate  # HumanGate, or None when increases are disabled
        self.resolution = resolution  # how long a bar is; None means daily

    def plan(
        self,
        cards: Sequence[Scorecard],
        firms: Sequence[FirmRecord],
        as_of=None,
    ) -> list:
        """Work out every change without touching anything.

        ``as_of`` is the market's timestamp, and it is what stops the same
        judgement being charged for on every tick. Without it the cadence guard
        cannot run, so a caller that omits it gets one review per call — which
        is what the tests want and what production must not have.
        """
        by_id = {f.id: f for f in firms}
        deployed = sum((D(f.allocation) for f in firms if not f.is_killed), ZERO)
        headroom = money(D(self.config.total_capital) - deployed)
        bar = self._bar_key(as_of)
        changes: list = []

        for card in cards:
            firm = by_id.get(card.firm_id)
            if firm is None or firm.is_killed:
                continue
            current = D(firm.allocation)

            if not card.sufficient_data:
                continue  # still being measured; capital does not move

            # A paused firm is not trading, so its score is frozen and it
            # qualifies for the same cut on every bar from now until the
            # minimum — a ratchet that punishes it for a number it has been
            # forbidden from changing. Its capital comes back via release().
            if not firm.is_active:
                continue

            # One judgement per bar. Thirteen ticks inside one bar is thirteen
            # readings of the same score, not thirteen pieces of evidence.
            if bar and self._already_moved(firm, bar):
                continue

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
                        bar=bar,
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
                        payload={"target": str(target), "cash": str(firm.cash),
                                 "bar": bar},
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
                        bar=bar,
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
                        "bar": change.bar,
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
                    "bar": change.bar,
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

    def _bar_key(self, as_of) -> str:
        if as_of is None:
            return ""
        resolution = self.resolution
        if resolution is None:
            from ..resolution import DAILY

            resolution = DAILY
        return resolution.bar_key(as_of) or ""

    def _already_moved(self, firm: FirmRecord, bar: str) -> bool:
        """Has this firm's capital already been judged on this bar?

        Read from the event log rather than kept in memory, because the tick
        loop is restarted routinely and a guard that resets on restart is a
        guard that scales with how often you deploy.

        A deferred cut counts. The firm was reviewed and the answer was "not
        today"; asking again four seconds later cannot produce a better one,
        and re-asking is exactly how the cut ended up compounding.

        The bar is read back out of the payload rather than derived from the
        row's ``created_at``. Those are two different clocks — the market's and
        the machine's — and this whole class of bug is what happens when one is
        quietly substituted for the other.
        """
        try:
            rows = self.store.db.query(
                "SELECT payload FROM brokerage_events WHERE firm_id = ? "
                "AND event_type IN ('allocation', 'allocation_deferred', "
                "'allocation_requested') ORDER BY id DESC LIMIT 8",
                (firm.id,),
            )
        except Exception:  # noqa: BLE001 - never fail a pass over a guard
            return False
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if isinstance(payload, dict) and payload.get("bar") == bar:
                return True
        return False

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
