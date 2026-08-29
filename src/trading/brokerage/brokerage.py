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

from ...money import D, fmt_money, money
from ..config import TradingConfig
from ..data.market_data import MarketData
from ..firms import strikes
from ..firms.kill_switch import INSUFFICIENT_DATA, should_kill_firm
from ..firms.strikes import StrikeConfig
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
        self.strike_config = StrikeConfig()
        self.allocator = Allocator(
            store, self.config.brokerage, gate, self.config.data.resolution
        )
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
        feed_name = str(getattr(getattr(market, "feed", None), "name", "") or "")
        for card in report.cards:
            self.evaluator.persist(card, feed_name, market.as_of())

        report.paused, report.kills_requested = self.check_kills(firms, report.cards)

        halted, reason, state = self.kill_switch.check(report.cards)
        if halted:
            report.halted = self.kill_switch.halt(reason, state)
            # A halted ecosystem does not reallocate. Capital decisions during
            # a stop are exactly the decisions a stop exists to prevent.
            report.leaderboard = Leaderboard(report.cards, self.store.firms())
            return report

        changes = self.allocator.plan(report.cards, firms, market.as_of())
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
            should, reason = should_kill_firm(card.to_metrics(), self.config.kill)
            if not should or reason == INSUFFICIENT_DATA:
                # Back above the line. The next slump is a new episode, and
                # earns its own strike — see firms/strikes.py for why this
                # matters more than it looks.
                strikes.clear_breach(self.store, firm)
                continue

            outcome = strikes.record_breach(self.store, firm, reason, self.strike_config)
            if outcome is None:
                continue        # same slump, already answered for

            if firm.is_active:
                self.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
            self.store.record_event(
                "strike",
                outcome.describe(),
                firm_id=firm.id,
                payload={"reason": reason, "strikes": outcome.strikes,
                         "bars": outcome.bars_left, "score": str(card.score)},
            )
            paused.append({"firm": firm.firm_key, "reason": outcome.describe()})

            # Only the last strike asks for a kill. The first two are sentences
            # the firm serves and comes back from — a village that destroys a
            # strategy the first time it has a bad fortnight gets quieter every
            # time it is wrong, and it has already been wrong six times.
            if outcome.terminated:
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

    # -- real money --------------------------------------------------------
    def request_promotion(self, firm_key: str, readiness, venue: str, by: str):
        """Ask a human to put one firm on a live venue. Grants nothing.

        Refuses to even *file* the request unless every criterion is met. That
        is deliberate and it is the same reasoning as not filing a kill request
        on a blind book: an approval put in front of somebody is an implicit
        claim that the evidence supports it, and a human who approves nine
        unsupported requests has been misled by their own system.
        """
        if not readiness.ready:
            raise ValueError(
                f"{firm_key} does not meet the criteria for live trading "
                f"({len(readiness.failures)} unmet: "
                f"{', '.join(c.name for c in readiness.failures[:3])}). "
                "Nothing has been requested. See `trade live-status`."
            )
        if self.gate is None:
            raise ValueError("live promotion needs a human gate; none was configured")
        from ...agents.human_gate import ApprovalAction

        return self.gate.request(
            ApprovalAction.LIVE_TRADING.value,
            f"Put {firm_key} on {venue} with REAL MONEY, capped at "
            f"{fmt_money(readiness.start_capital)}",
            details={
                "firm": firm_key,
                "venue": venue,
                "capital": str(readiness.start_capital),
                "requested_by": by,
                "evidence": {c.name: str(c.value) for c in readiness.checks},
            },
            dedupe_key=f"golive:{firm_key}",
        )

    def promote_firm(self, firm_key: str, venue: str, capital, by: str = "human") -> dict:
        """Carry out an approved promotion. Only ever called from the gate.

        **A firm goes live flat.** Its paper positions were bought with money
        that does not exist, and the live venue has never heard of them: the
        first order against one would either be rejected or — far worse — go
        short with real stock. So an open book refuses the promotion rather
        than carrying fiction across the boundary. Flatten it, then go.

        **The mandate is capped, not transferred.** A firm running $100,000 on
        paper does not get $100,000 of real money because it did well at
        pretend. The cap is applied to *cash*, because cash is what the firm
        can actually spend — capping the allocation alone would leave the
        number small and the buying power untouched. The difference is handed
        back to the brokerage exactly the way a dead firm's stake is, so the
        reconciler's identity (cash = allocation + Σ cash_delta) still holds
        afterwards. Raising it later is another trip through the gate, like
        every other increase in risk.
        """
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        open_positions = [p for p in self.store.positions(firm.id) if p.is_open]
        if open_positions:
            raise ValueError(
                f"{firm_key} holds {len(open_positions)} open position(s) "
                f"({', '.join(p.symbol for p in open_positions[:4])}) bought with "
                "paper money. A firm goes live flat — close the book first."
            )

        cash = money(firm.cash)
        capped = money(min(D(capital), cash))
        withdrawn = money(cash - capped)
        if withdrawn > 0:
            # Same movement as releasing a dead firm's stake: allocation and
            # cash step together, so nothing downstream has to be told.
            self.store.set_allocation(
                firm.id, money(D(firm.allocation) - withdrawn), -withdrawn
            )
        self.store.update_firm_fields(firm.id, venue=venue)
        self.store.record_event(
            "promoted_live",
            f"{firm_key} moved to {venue} with real money, capped at {fmt_money(capped)}, "
            f"approved by {by}",
            firm_id=firm.id,
            payload={"venue": venue, "capital": str(capped), "by": by,
                     "was_venue": firm.venue, "was_allocation": str(firm.allocation),
                     "returned": str(withdrawn)},
        )
        return {"firm": firm_key, "venue": venue, "capital": str(capped), "by": by,
                "returned": str(withdrawn)}

    def demote_firm(self, firm_key: str, reason: str) -> dict:
        """Put a firm back on paper. Needs nobody's permission, ever.

        This is the other half of the asymmetry and the reason the first half
        is acceptable. Going live takes evidence and a human; coming back takes
        a reason and happens immediately. Anything that can notice trouble may
        call it — the tick, the reconciler, an operator, a button.

        Nothing can block it, including an open book. It stops the firm from
        sending anything further to the live venue, which is the part that has
        to be instant; it does **not** liquidate. Selling a real position
        automatically is how a data outage turns into a realised loss — that is
        precisely the accident that killed a village of eleven — so any real
        stock still held is reported, loudly, in `still_open`, for a person to
        close deliberately.
        """
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        if firm.venue == "paper":
            return {"firm": firm_key, "venue": "paper", "changed": False,
                    "still_open": []}
        still_open = [p.symbol for p in self.store.positions(firm.id) if p.is_open]
        self.store.update_firm_fields(firm.id, venue="paper")
        self.store.record_event(
            "demoted_to_paper",
            f"{firm_key} returned to paper: {reason}"
            + (f" — {len(still_open)} position(s) still open at {firm.venue}: "
               f"{', '.join(still_open)}" if still_open else ""),
            firm_id=firm.id,
            payload={"reason": reason, "was_venue": firm.venue,
                     "still_open": still_open},
        )
        return {"firm": firm_key, "venue": "paper", "changed": True,
                "reason": reason, "was_venue": firm.venue,
                "still_open": still_open}

    def live_firms(self) -> list:
        return [f for f in self.store.firms() if f.venue != "paper"]

    def all_to_paper(self, reason: str = "operator pulled everything back") -> list:
        """The one button you want at three in the morning."""
        return [self.demote_firm(f.firm_key, reason) for f in self.live_firms()]

    def revive_firm(self, firm_key: str, by: str, market: MarketData) -> dict:
        """Reverse a kill that was decided on numbers that were not real.

        Killed is terminal, and stays terminal. This is not an appeal against a
        verdict — it is the narrow case where the *evidence* was fiction: a feed
        that went dark marks every position to zero, which reads as a total
        drawdown, and the kill switch acts on that before the sample gate
        because emptying the account is the one thing it must never wait on.
        An Alpaca outage killed a village of eleven that way.

        So the test is not "did a human ask nicely". It is **would this firm be
        killed today, by the same rules, against a feed that can price its
        book?** If yes, the kill stands and this refuses. If the feed still
        cannot price it, this refuses too — reviving into blindness would just
        re-run the same accident. Only a firm the rules now clear comes back.

        **It restores status and nothing else.** The allocator released this
        firm's capital when it died, and giving it back is an increase in risk,
        which needs a human at the gate exactly as it always did. A revived
        firm is an active firm with whatever mandate it has left.
        """
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        if not firm.is_killed:
            raise ValueError(f"{firm_key} is not killed; use resume to un-pause it")

        card = self.evaluator.evaluate(firm, market)
        if not card.can_be_valued:
            raise ValueError(
                f"{firm_key} still holds {', '.join(card.unpriceable)}, which the feed "
                "cannot price. Fix the feed first — reviving now would re-run the "
                "same accident on the next tick."
            )
        would_die, why = should_kill_firm(card.to_metrics(), self.config.kill)
        if would_die:
            raise ValueError(
                f"{firm_key} would be killed again on today's numbers ({why}). "
                "The kill stands."
            )

        self.store.set_firm_status(firm.id, FirmStatus.ACTIVE.value)
        self.store.record_event(
            "revive",
            f"{firm_key} revived by {by}: {firm.kill_reason or 'no reason recorded'} "
            f"could not be reproduced against a working feed",
            firm_id=firm.id,
            payload={
                "by": by,
                "original_reason": firm.kill_reason or "",
                "equity_now": str(card.equity),
                "drawdown_now": str(card.drawdown_pct),
            },
        )
        return {
            "firm": firm_key,
            "status": FirmStatus.ACTIVE.value,
            "by": by,
            "was": firm.kill_reason or "",
            "allocation": str(firm.allocation),
        }

    def resume_firm(self, firm_key: str, by: str) -> dict:
        """Un-pause a firm. Only ever called from an explicit human command."""
        firm = self.store.get_firm(firm_key)
        if firm is None:
            raise ValueError(f"unknown firm {firm_key}")
        if firm.is_killed:
            raise ValueError(
                f"{firm_key} is killed; killed firms do not resume. "
                "If it was killed on a feed outage, see `trade revive`."
            )
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
