"""The ecosystem — the loop that ties every layer together.

One tick, in order:

    market data  →  firms propose
                 →  heart reviews every proposal
                 →  venue fills what survived
                 →  ledger settles (position, cash, P&L, all or nothing)
                 →  brain remembers
                 →  brokerage reconciles, scores, kills, allocates
                 →  audit trail written

The order is the safety argument. Ethics runs before execution, settlement is
atomic, and the brokerage refuses to do anything at all until the books
reconcile. Nothing after a failed reconciliation runs, including the parts
that would look like progress.

This is the ``src/agents/orchestrator.py`` of the trading edition, and it
follows the same rule: the orchestrator holds no cleverness, only order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from ..agents.human_gate import ApprovalAction, ApprovalStatus, HumanGate
from ..config import Config
from ..db.connection import Database, utcnow
from ..money import D, ZERO, fmt_money, money
from ..notifications import Notifier, build_notifier
from .audit.obsidian_logger import ObsidianLogger
from .backtest import Backtester
from .brain.evolver import Evolver
from .brain.learning import Learner
from .brain.memory import AgentMemory
from .brokerage.brokerage import Brokerage, OversightReport
from .brokerage.reconciliation import LedgerNotReconciled
from .config import TradingConfig
from .data.market_data import MarketData
from .data.feeds import build_feed
from .execution import build_venue
from .execution.live import LiveTradingNotApproved, VenueNotConfigured
from .firms.firm import Firm
from .firms.spec import FirmSpec, load_firm_specs
from .gateway.omniroute import OmniRoute
from .heart.ethics import Heart
from .models import FirmRecord, FirmStatus, ProposalStatus
from .store import TradingLedgerError, TradingStore


@dataclass
class TickReport:
    started_at: Optional[datetime] = None
    proposals: int = 0
    filled: int = 0
    blocked_by_ethics: int = 0
    blocked_by_risk: int = 0
    refused_by_venue: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    oversight: Optional[OversightReport] = None
    lessons: int = 0

    def summary(self) -> str:
        lines = [
            f"tick @ {self.started_at:%Y-%m-%d %H:%M} — {self.proposals} proposal(s), "
            f"{self.filled} filled, {self.blocked_by_risk} blocked by risk, "
            f"{self.blocked_by_ethics} blocked by ethics"
        ]
        for refusal in self.refused_by_venue:
            lines.append(f"  VENUE REFUSED: {refusal}")
        for err in self.errors:
            lines.append(f"  ERROR: {err}")
        if self.oversight is not None:
            lines.append(self.oversight.summary())
        if self.lessons:
            lines.append(f"  {self.lessons} new lesson(s) recorded")
        return "\n".join(lines)


class Ecosystem:
    def __init__(
        self,
        db: Database,
        config: Optional[TradingConfig] = None,
        app_config: Optional[Config] = None,
        notifier: Optional[Notifier] = None,
        gate: Optional[HumanGate] = None,
    ):
        self.db = db
        self.config = config or TradingConfig()
        self.app_config = app_config or Config()
        self.notifier = notifier if notifier is not None else build_notifier(self.app_config)
        self.gate = gate or HumanGate(db, self.notifier, self.app_config.gate, self.app_config)
        self.store = TradingStore(db)
        self.heart = Heart(self.store, self.config)
        self.memory = AgentMemory(self.store, self.config.brain)
        self.learner = Learner(self.store, self.memory, self.config)
        self.evolver = Evolver(self.store, self.config)
        self.brokerage = Brokerage(self.store, self.config, self.gate)
        self.gateway = OmniRoute(self.config.gateway)
        self.audit = ObsidianLogger(self.config.audit_vault, self.config)
        self._feed = None
        self._specs: dict = {}
        self._flow = None
        self._court = None
        self._tokens = None
        self._arena = None
        self._market = None
        self._sandbox = None

    # =====================================================================
    # the layers above the ledger
    #
    # All four are built lazily and none is on the tick's critical path. The
    # tick trades, reconciles and allocates exactly as it did before any of
    # them existed; these are where files get judged, points get awarded and
    # firms plot against each other.
    # =====================================================================
    @property
    def flow(self):
        """The tick's telemetry. Best-effort: it never raises into a decision."""
        if self._flow is None:
            from .flow import FlowRecorder

            self._flow = FlowRecorder(self.db)
        return self._flow

    @property
    def court(self):
        if self._court is None:
            from .court import StrategyCourt

            self._court = StrategyCourt(self.store, self.config, self.memory)
        return self._court

    @property
    def tokens(self):
        if self._tokens is None:
            from .competition import TokenLedger

            self._tokens = TokenLedger(self.db)
        return self._tokens

    @property
    def arena(self):
        if self._arena is None:
            from .competition import Arena

            self._arena = Arena(self.db, tokens=self.tokens)
        return self._arena

    @property
    def black_market(self):
        # Deliberately not `market` — that is the price feed, and confusing the
        # two would be an unusually bad name collision in this particular file.
        if self._market is None:
            from .black_market import BlackMarket

            self._market = BlackMarket(self.store, self.config, self.tokens, self.gate)
        return self._market

    @property
    def sandbox(self):
        if self._sandbox is None:
            from .sandbox import Sandbox

            self._sandbox = Sandbox(self.store, self.tokens, seed=self.config.brain.seed)
        return self._sandbox

    def run_season(self, metric: str = "score") -> dict:
        """One competitive round: bouts and milestones over the current cards.

        Reads scorecards and writes tokens. It cannot move capital, pause a
        firm or touch a position, so a season can be run at any time without
        consequence for the ledger.
        """
        cards = self.brokerage.evaluator.evaluate_all(self.store.firms(), self.market())
        return {
            "bouts": self.arena.round_robin(cards, metric),
            "milestones": self.arena.award_milestones(cards),
            "standings": self.arena.standings(),
        }

    # =====================================================================
    # setup
    # =====================================================================
    @property
    def feed(self):
        if self._feed is None:
            self._feed = build_feed(self.config.data)
        return self._feed

    def market(self, symbols: Sequence[str] = (), cursor: int = -1) -> MarketData:
        universe = list(symbols) or self.universe()
        return MarketData(self.feed, universe, cursor=cursor)

    def universe(self) -> list:
        out: list = []
        for firm in self.store.firms():
            for symbol in firm.universe:
                if symbol not in out:
                    out.append(symbol)
        return out

    def init_firms(self, specs: Optional[Sequence[FirmSpec]] = None) -> list:
        """Create or refresh firms from config. Never re-funds a live firm."""
        specs = list(specs if specs is not None else load_firm_specs(config=self.config))
        records = []
        for spec in specs:
            self._specs[spec.firm_key] = spec
            existing = self.store.get_firm(spec.firm_key)
            record = FirmRecord(
                firm_key=spec.firm_key,
                name=spec.name,
                asset_class=spec.asset_class,
                strategy=spec.strategy,
                venue=spec.venue,
                risk_limit=spec.risk_limit,
                initial_allocation=spec.allocation,
                allocation=spec.allocation,
                cash=spec.allocation,
                high_water_mark=spec.allocation,
                genome=spec.genome,
                universe=list(spec.universe),
            )
            saved = self.store.upsert_firm(record)
            if existing is None:
                self.store.record_event(
                    "firm_created",
                    f"{spec.firm_key} funded with {fmt_money(spec.allocation)} "
                    f"({spec.asset_class or 'unspecified'}, venue {spec.venue})",
                    firm_id=saved.id,
                    payload={"universe": list(spec.universe), "genome": spec.genome},
                )
            records.append(saved)
        return records

    def specs(self) -> dict:
        if not self._specs:
            try:
                for spec in load_firm_specs(config=self.config):
                    self._specs[spec.firm_key] = spec
            except Exception:  # noqa: BLE001 - a missing config is not fatal here
                pass
        return self._specs

    def build_firm(self, record: FirmRecord) -> Firm:
        spec = self.specs().get(record.firm_key)
        if spec is not None:
            return Firm.from_spec(spec, record, self.config)
        return Firm(record, limits=self.config.firm, kill_config=self.config.kill)

    # =====================================================================
    # the tick
    # =====================================================================
    def tick(self, market: Optional[MarketData] = None) -> TickReport:
        report = TickReport(started_at=utcnow())
        market = market or self.market()
        self.heart.new_session()

        # Telemetry for the flow diagram. Every emit() below is best-effort and
        # swallows its own errors — the tick's behaviour is identical whether
        # or not anybody is watching.
        flow = self.flow
        flow.start_run()
        flow.emit("market", f"bars up to {market.as_of()}", detail=f"source: {self.feed.name}")

        for record in self.store.firms():
            if record.is_killed and not self.store.positions(record.id):
                continue  # dead and flat: nothing left to do
            try:
                self._run_firm(record, market, report)
            except TradingLedgerError as exc:
                report.errors.append(f"{record.firm_key}: {exc}")
                flow.emit("ledger", "ledger refused a fill", kind="alarm",
                          firm=record.firm_key, detail=str(exc))

        try:
            report.oversight = self.brokerage.oversee(market)
            flow.move("brain", "brokerage", "reconciled, scored, allocated")
        except LedgerNotReconciled as exc:
            report.errors.append(str(exc))
            flow.move("brain", "brokerage", "books do not reconcile", kind="alarm",
                      detail=str(exc)[:300])
            self.notifier.send("🚨 TRADING LEDGER DOES NOT RECONCILE", str(exc))
            return report

        for change in report.oversight.allocation_changes:
            if change.applied:
                flow.emit("brokerage", f"cut {change.firm_key} to {change.new_allocation}",
                          firm=change.firm_key, detail=str(change))
            else:
                flow.move("brokerage", "gate", f"raise for {change.firm_key} needs you",
                          kind="blocked", firm=change.firm_key, detail=str(change))
        for paused in report.oversight.paused:
            flow.move("brokerage", "gate", f"{paused['firm']} paused — kill needs you",
                      kind="alarm", firm=paused["firm"], detail=paused["reason"])

        report.lessons = self.learner.record(self.learner.lessons(report.oversight.cards))
        self.audit.log_oversight(report.oversight, market.as_of())
        self.audit.rebuild_index()
        flow.move("brokerage", "audit", "vault written")

        if report.oversight.halted:
            flow.move("brokerage", "halt", report.oversight.halted["reason"][:100],
                      kind="alarm")
        if report.oversight.halted:
            self.notifier.send(
                "🛑 TRADING KILL_ALL", report.oversight.halted["reason"]
            )
        for paused in report.oversight.paused:
            self.notifier.send(
                f"🚨 FIRM PAUSED: {paused['firm']}",
                f"{paused['firm']} tripped its kill switch: {paused['reason']}\n\n"
                "Trading is paused. The firm has NOT been killed. Approve or reject the "
                "pending kill_firm request:\n\n"
                "  python -m src.main approvals\n"
                "  python -m src.main approve <id> --by you\n",
            )
        return report

    def _run_firm(self, record: FirmRecord, market: MarketData, report: TickReport) -> None:
        firm = self.build_firm(record)
        market.register(record.universe)
        positions = self.store.positions(record.id)
        venue = build_venue(record.venue, self.config, self.gate)

        flow = self.flow
        for proposal in firm.propose(market, positions):
            report.proposals += 1
            what = f"{proposal.side} {proposal.symbol}"
            flow.move("market", "firms", what, firm=record.firm_key,
                      detail=proposal.rationale[:200])
            self.heart.consider(proposal, record, positions, market)
            self.store.record_proposal(proposal)
            flow.move("firms", "heart", what, firm=record.firm_key,
                      detail=f"risk: {proposal.risk_verdict} — {proposal.risk_reason}")

            if proposal.risk_verdict == "block":
                report.blocked_by_risk += 1
                self.audit.log_trade(record.firm_key, proposal)
                flow.move("heart", "blocked", f"risk blocked {what}", kind="blocked",
                          firm=record.firm_key, detail=proposal.risk_reason)
                continue
            if proposal.ethics_verdict == "block":
                report.blocked_by_ethics += 1
                self.audit.log_trade(record.firm_key, proposal)
                flow.move("heart", "blocked", f"ethics blocked {what}", kind="blocked",
                          firm=record.firm_key, detail=proposal.ethics_reason)
                continue
            if not proposal.is_executable:
                continue
            flow.move("heart", "venue", what, firm=record.firm_key,
                      detail=f"ethics: {proposal.ethics_verdict} — {proposal.ethics_reason}")

            try:
                fill = venue.execute(proposal, market.mark(proposal.symbol))
            except (LiveTradingNotApproved, VenueNotConfigured) as exc:
                # Refusing to trade is a safe outcome and is recorded as one.
                proposal.status = ProposalStatus.BLOCKED.value
                self.store.set_proposal_status(proposal.id, ProposalStatus.BLOCKED.value)
                report.refused_by_venue.append(str(exc))
                self.audit.log_trade(record.firm_key, proposal)
                flow.move("venue", "blocked", f"{record.venue} refused {what}",
                          kind="refused", firm=record.firm_key, detail=str(exc))
                continue

            try:
                fill = self.store.settle(record, fill)
            except TradingLedgerError as exc:
                # The ledger refused this one fill. Record it against the
                # proposal and carry on with the firm's other symbols — one
                # unaffordable order is not a reason to stop the firm's exits.
                proposal.status = ProposalStatus.BLOCKED.value
                self.store.set_proposal_status(proposal.id, ProposalStatus.BLOCKED.value)
                report.errors.append(f"{record.firm_key}: {exc}")
                self.audit.log_trade(record.firm_key, proposal)
                flow.move("venue", "blocked", f"ledger refused {what}", kind="alarm",
                          firm=record.firm_key, detail=str(exc))
                continue
            report.filled += 1
            flow.move("venue", "ledger", f"filled {fill.quantity} {fill.symbol}",
                      firm=record.firm_key,
                      detail=f"at {fill.price}, fee {fill.fee}, cash {fill.cash_delta}")
            # settle() moved the database row; keep the object in step so the
            # audit note records what happened rather than what was intended.
            proposal.status = ProposalStatus.FILLED.value
            self.memory.remember_fill(fill, proposal)
            positions = self.store.positions(record.id)
            flow.move("ledger", "brain", f"remembered {fill.symbol}", firm=record.firm_key,
                      detail=f"realised {fill.realized_pnl}")
            narration = self.gateway.narrate(proposal, proposal.signals)
            self.audit.log_trade(record.firm_key, proposal, fill, narration.text)

    # =====================================================================
    # operator actions
    # =====================================================================
    def apply_approvals(self) -> list:
        """Carry out decisions a human has already made.

        Nothing here decides anything: it reads approved rows and executes the
        consequence, then marks the row applied so it cannot run twice.
        """
        applied = []
        for row in self.db.query(
            "SELECT * FROM human_approvals WHERE status = ? ORDER BY id",
            (ApprovalStatus.APPROVED.value,),
        ):
            approval = self.gate.get(row["id"])
            details = approval.details or {}
            if details.get("applied"):
                continue

            if approval.action == ApprovalAction.KILL_FIRM.value and details.get("firm"):
                result = self.brokerage.kill_firm(
                    details["firm"], details.get("reason", "approved by human")
                )
                applied.append(f"killed {result['firm']} (returned {result['returned']})")
            elif approval.action == ApprovalAction.ALLOCATE_CAPITAL.value and details.get("firm"):
                change = self.brokerage.allocator.apply_approved_increase(
                    details["firm"], D(details["new_allocation"])
                )
                applied.append(str(change))
            else:
                continue

            details["applied"] = True
            self.db.update("human_approvals", approval.id, {"details": json.dumps(details, default=str)})
        return applied

    def request_live_trading(self, venue_name: str, reason: str = ""):
        """Ask for permission to trade a live venue. Sends no order."""
        venue = build_venue(venue_name, self.config, self.gate)
        if not getattr(venue, "is_live", False):
            raise ValueError(f"{venue_name} is not a live venue; it needs no approval")
        return venue.request_approval(reason)

    def evolve(self, firm_key: Optional[str] = None, generations: int = 1) -> list:
        """Run the evolver. Refuses while the books are broken.

        Evolution reads the same ledger every other decision reads, and it
        changes what the firms will do next. Doing that on unreconciled books
        would bake a bad number into the strategy itself.
        """
        market = self.market()
        self.brokerage.require_reconciled(market)
        out = []
        firms = [self.store.get_firm(firm_key)] if firm_key else self.store.firms()
        for record in firms:
            if record is None or record.is_killed:
                continue
            spec = self.specs().get(record.firm_key)
            analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
            for generation in range(1, generations + 1):
                out.append(self.evolver.evolve(record, market, generation, analysts))
                record = self.store.require_firm_by_id(record.id)
        return out

    def simulate(self, days: int = 30, on_tick=None) -> list:
        """Replay the last N bars through the *whole* ecosystem, one tick per bar.

        Not a backtest. ``backtest`` measures one firm's strategy in isolation
        and writes nothing; this drives the real loop — ledger, ethics,
        brokerage, kill switches, allocations, audit trail — over historical
        bars, and everything it does is permanent. It is how you exercise the
        oversight layer without waiting a month for the data to arrive.
        """
        market = self.market()
        total = market.length()
        if total <= 1:
            return []
        start = max(1, total - days)
        reports = []
        for index in range(start, total):
            market.seek(index)
            report = self.tick(market)
            reports.append(report)
            if on_tick is not None:
                on_tick(index, report)
            if report.oversight is not None and report.oversight.halted:
                break  # a halted ecosystem does not keep trading
        return reports

    def backtest(self, firm_key: Optional[str] = None, days: Optional[int] = None) -> list:
        """Backtest every firm (or one) on the configured data. Writes nothing."""
        backtester = Backtester(self.config)
        results: list = []
        firms = [self.store.get_firm(firm_key)] if firm_key else self.store.firms()
        for record in firms:
            if record is None:
                continue
            spec = self.specs().get(record.firm_key)
            market = MarketData(self.feed, record.universe)
            results.append(
                backtester.run(
                    firm_key=record.firm_key,
                    symbols=record.universe,
                    market=market,
                    genome=record.genome,
                    analysts=spec.analysts if spec else ("technical", "sentiment", "macro"),
                    capital=record.initial_allocation or self.config.firm.allocation,
                    risk_limit=record.risk_limit,
                    steps=days,
                )
            )
        return results

    # =====================================================================
    # reporting
    # =====================================================================
    def status(self) -> dict:
        market = self.market()
        firms = self.store.firms()
        cards = self.brokerage.evaluator.evaluate_all(firms, market)
        reconciliation = self.brokerage.reconcile(market)
        return {
            "as_of": market.as_of(),
            "data_source": self.feed.name,
            "firms": len(firms),
            "active": sum(1 for f in firms if f.is_active),
            "paused": sum(1 for f in firms if f.status == FirmStatus.PAUSED.value),
            "killed": sum(1 for f in firms if f.is_killed),
            "equity": money(sum((D(c.equity) for c in cards), ZERO)),
            "capital": money(sum((D(f.allocation) for f in firms), ZERO)),
            "reconciled": reconciliation.ok,
            "reconciliation": reconciliation.summary(),
            "pending_approvals": len(self.gate.pending()),
            "gateway": self.gateway.health(),
            "cards": cards,
        }

    def audit_report(self) -> str:
        market = self.market()
        firms = self.store.firms()
        cards = self.brokerage.evaluator.evaluate_all(firms, market)
        lines = [
            "# Trading ecosystem audit",
            "",
            f"Generated {utcnow():%Y-%m-%d %H:%M}Z from {self.db.url} ({self.db.dialect}).",
            "",
            "## Reconciliation",
            "",
            self.brokerage.reconcile(market).summary(),
            "",
            "## Firms",
            "",
        ]
        for card in cards:
            firm = next((f for f in firms if f.id == card.firm_id), None)
            lines += [
                f"### {card.firm_key} — {firm.name if firm else ''} ({firm.status if firm else '?'})",
                "",
                f"- Allocation: {fmt_money(firm.allocation) if firm else '?'}",
                f"- Equity: {fmt_money(card.equity)} (cash {fmt_money(card.cash)}, "
                f"positions {fmt_money(card.market_value)})",
                f"- Realised: {fmt_money(card.realized_pnl)}, "
                f"unrealised: {fmt_money(card.unrealized_pnl)}, fees: {fmt_money(card.fees)}",
                f"- Score {card.score}, return {card.return_pct}%, drawdown {card.drawdown_pct}%",
                f"- Trades: {card.trades} ({card.closed_trades} closed)"
                + ("" if card.sufficient_data else " — below the sample gate"),
                "",
            ]
        lines += ["## Ethics", "", self.heart.compliance.render(), ""]
        lines += ["## Events", ""]
        for event in self.store.events(limit=40):
            lines.append(f"- `{event.get('created_at')}` **{event.get('event_type')}**: {event.get('detail')}")
        return "\n".join(lines)


__all__ = ["Ecosystem", "TickReport"]
