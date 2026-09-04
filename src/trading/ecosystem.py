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
from .council import COUNCIL_ACTIONS, Council, gather as gather_evidence
from .data.market_data import MarketData
from .data.feeds import build_feed
from .execution import build_venue
from .execution.live import LiveTradingNotApproved, VenueNotConfigured
from .firms import bankruptcy
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
    rulings: list = field(default_factory=list)
    carried_out: list = field(default_factory=list)
    village: list = field(default_factory=list)
    bot_notes: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    evolved: list = field(default_factory=list)
    expiries: list = field(default_factory=list)

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
        for ruling in self.rulings:
            lines.append(f"  COUNCIL: {ruling}")
        for done in self.carried_out:
            lines.append(f"  CARRIED OUT: {done}")
        for happening in self.village:
            lines.append(f"  VILLAGE: {happening}")
        for note in self.bot_notes:
            lines.append(f"  BOT: {note}")
        for note in self.signals:
            lines.append(f"  SCANNER: {note}")
        for note in self.evolved:
            lines.append(f"  BRAIN: {note}")
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
        self.council = Council(db, margin=self.config.autonomy.margin)
        if self.config.autonomy.council_decides:
            self.gate.quiet_actions = set(COUNCIL_ACTIONS)
        self.gateway = OmniRoute(self.config.gateway)
        self.audit = ObsidianLogger(self.config.audit_vault, self.config)
        self._feed = None
        self._specs: dict = {}
        self._flow = None
        self._court = None
        self._tokens = None
        self._arena = None
        self._living = None
        self._settings = None
        self._market = None
        self._sandbox = None
        self._signals = None
        self._scanners = None
        self._scribe = None
        self._news = None
        self._shadow = None
        #: Which unpriceable symbols have already been reported on this bar.
        #: Presentation state, deliberately in memory — see the tick.
        self._reported_bar = ""
        #: The furthest bar this run has seen. `market.as_of()` is the max
        #: timestamp across symbols, and it is **not monotonic**: a partial bar
        #: is present in one fetch and absent from the next, so the maximum
        #: flips back and forth. Measured live on 2026-09-01, the village's bar
        #: alternated 16:00 / 16:15 / 16:00 / 16:15 within single minutes.
        #:
        #: Everything that asks "have I already done this bar?" is defeated by
        #: that, because each flip looks like a new bar. It drained an 80-bar
        #: gulag sentence in about two hours instead of twenty, and it bills
        #: borrow once per alternating key rather than once per bar. Time does
        #: not run backwards, so the bar the village acts on does not either.
        self._high_bar = ""
        self._reported_blind: set = set()

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
    def settings(self):
        if self._settings is None:
            from .settings import Settings

            self._settings = Settings(self.db)
        return self._settings

    @property
    def signals(self):
        """The published-signal board. Read by the firms, written by scanners."""
        if self._signals is None:
            from .signals import SignalBoard

            self._signals = SignalBoard(self.db)
        return self._signals

    @property
    def shadow(self):
        """The paper options desk, or None when it has not been switched on.

        Off unless `TRADE_SHADOW_ENABLED` is set, and off by default for the
        same reason the news desk is: it reaches the open internet on its own,
        once per underlying per bar, and this village's rate limit has been the
        proximate cause of more than one outage.
        """
        if self._shadow is None:
            import os

            if not os.environ.get("TRADE_SHADOW_ENABLED", "").strip():
                return None
            from .shadow import ShadowDesk

            # It runs on the village's own genome so the evolver's mutations
            # reach it — a desk reading module constants is a desk that cannot
            # learn, which is what the first version of this was.
            from .brain.evolver import BASE_GENOME

            self._shadow = ShadowDesk(self.store, self.signals,
                                      genome=dict(BASE_GENOME))
        return self._shadow

    @property
    def news(self):
        """The news desk, or None when it has not been switched on.

        Opt-in by environment rather than by config file, because this is the
        first thing in the village that reaches out to the open internet on
        its own, and that should be a decision somebody made on purpose.
        """
        if self._news is None:
            import os

            if not os.environ.get("TRADE_NEWS_ENABLED", "").strip():
                return None
            from .news import NewsDesk

            self._news = NewsDesk(self.signals)
        return self._news

    @property
    def scribe(self):
        """Reads the bankruptcy postmortems nothing else has ever read."""
        if self._scribe is None:
            from .scribe import Scribe

            self._scribe = Scribe(self.store, self.signals)
        return self._scribe

    @property
    def scanners(self):
        """The configured scanners. A village with none is the normal case.

        A malformed `scanners:` block is caught here rather than allowed out of
        a tick: the tick reports it as a scanner note and keeps trading, which
        is the correct blast radius for a screener that will not load.
        """
        if self._scanners is None:
            from .signals import Scanners, ScannerError, load_scanner_specs

            specs, error = [], ""
            try:
                specs = load_scanner_specs(config=self.config)
            except ScannerError as exc:
                error = f"scanner config unusable, no scanner is running: {exc}"
            self._scanners = Scanners(self.signals, specs, error=error)
        return self._scanners

    @property
    def living(self):
        if self._living is None:
            from .living import Living

            self._living = Living(self)
        return self._living

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
        bouts = self.arena.round_robin(cards, metric)
        milestones = self.arena.award_milestones(cards)
        for fight in bouts:
            self.flow.move(
                "firms", "arena", str(fight)[:110],
                kind="ok" if fight.decided else "blocked",
                firm=fight.winner or fight.challenger,
            )
        for firm_key, milestone, amount in milestones:
            self.flow.move("firms", "arena", f"{milestone} (+{amount})", firm=firm_key)
        return {"bouts": bouts, "milestones": milestones, "standings": self.arena.standings()}

    # -- the layers, with the village watching -----------------------------
    #
    # Thin wrappers so a court ruling or a market sale shows up in the village
    # whether it was driven from the CLI or from the dashboard. Each one emits
    # and delegates; none of them changes what the underlying call does.
    def try_strategy(self, path, firm_key: str = "", submitted_by: str = ""):
        from pathlib import Path as _Path

        self.flow.move("firms", "court", f"{_Path(str(path)).name} submitted",
                       firm=firm_key, detail=f"by {submitted_by or 'unknown'}")
        case = self.court.submit(path, firm_key=firm_key, submitted_by=submitted_by)
        kind = {"accept": "ok", "modify": "blocked", "reject": "refused"}.get(
            case.verdict, "ok"
        )
        self.flow.emit("court", f"{case.verdict.upper()}: {case.evidence.name}", kind=kind,
                       firm=firm_key, detail=case.ruling.reason[:200])
        if case.verdict == "accept":
            self.flow.move("court", "brain", f"candidate genome #{case.genome_id}",
                           firm=firm_key)
        return case

    def list_asset(self, seller: str, asset_type: str, price, title: str = ""):
        listing = self.black_market.list_asset(seller, asset_type, price, title=title)
        self.flow.move("firms", "bazaar",
                       f"{seller} lists {asset_type} for {listing.price} {listing.currency}",
                       firm=seller, detail=listing.title)
        return listing

    def buy_listing(self, buyer: str, listing_id: int):
        result = self.black_market.buy(buyer, listing_id)
        if result["settled"]:
            self.flow.move("firms", "bazaar", f"{buyer} bought listing #{listing_id}",
                           firm=buyer)
        else:
            # A capital sale does not settle in the bazaar. The villager carries
            # it on to the gatehouse, which is where it waits for a person.
            self.flow.move("firms", "bazaar", f"{buyer} wants listing #{listing_id}",
                           firm=buyer)
            self.flow.move("bazaar", "gate",
                           f"capital transfer needs approval #{result['approval_id']}",
                           kind="blocked", firm=buyer)
        return result

    def intrigue(self, action: str, actor: str, name: str = "", target: str = ""):
        if action == "form":
            members = [m.strip() for m in target.split(",") if m.strip()]
            result = self.sandbox.form(name, actor, members)
        elif action == "betray":
            result = self.sandbox.betray(actor, name)
        elif action == "spy":
            result = self.sandbox.spy(actor, target)
        elif action == "sabotage":
            result = self.sandbox.sabotage(actor, target)
        else:
            raise ValueError(f"unknown sandbox action {action!r}")
        self.flow.move(
            "firms", "tavern", str(result)[:110],
            kind="alarm" if action in ("betray", "sabotage") else "ok",
            firm=actor,
            detail="nothing in the tavern reaches the ledger",
        )
        return result

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
        """The firm, with the seats it is entitled to.

        Three sources, in order of authority. The YAML is the operator's word
        and wins. Failing that, the genome — which is how a bankruptcy heir
        carries its predecessor's seats, since it is not in the YAML and never
        will be. Only a firm with neither falls back to the default.

        That fallback used to catch every heir: a single `technical` analyst
        and no signal board, so a successor could not hear the scanners, could
        not hear the scribe, and ran one indicator where its parent ran four.
        It inherited the genome, the universe and the lesson and had nothing
        left to think with.
        """
        from .firms.analysts import build_analysts

        spec = self.specs().get(record.firm_key)
        if spec is not None:
            return Firm.from_spec(spec, record, self.config, board=self.signals)

        seats = [str(s) for s in (record.genome or {}).get("analysts", []) or []]
        if seats:
            return Firm(
                record,
                analysts=build_analysts(seats, board=self.signals),
                limits=self.config.firm,
                kill_config=self.config.kill,
            )
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

        # A symbol whose bar is far behind its peers' is treated exactly like a
        # symbol with no bar at all, because that is what it is: a price nobody
        # can stand behind. Folding it in here rather than giving it a separate
        # path means the reporting below, the refusal to propose, and the
        # scorecard's "this book cannot be valued" all apply unchanged.
        resolution = self.config.data.resolution
        for symbol, behind in market.lagging(float(resolution.seconds)).items():
            market.unpriceable.setdefault(
                symbol,
                f"bar is {behind:.1f} bars behind the rest of the village — the "
                "other symbols are current, so this one is stale, not quiet",
            )
        # And the check the relative one cannot make. `lagging` compares a
        # symbol against its peers, so when the whole equity market is shut
        # every equity is equally stale, nothing is behind anything, and
        # nothing is flagged — which is how a firm came to sell EFA overnight
        # at a price 9.7 hours old, stamped with a bar the crypto feed had
        # advanced to. This is a freshness rule and not a clock: pre-market and
        # after-hours trading stay open, because a symbol printing in those
        # sessions has a fresh bar and never reaches this branch.
        for symbol, hours in market.stale().items():
            market.unpriceable.setdefault(
                symbol,
                f"newest bar is {hours:.1f}h old — nothing has traded this "
                "symbol for too long to stand behind the price",
            )

        # A symbol the feed cannot price is now a symbol with no bars rather
        # than an exception out of the whole tick — but it must not pass
        # unmentioned, and for a firm that *holds* the thing it is not a
        # detail: a position you cannot value is a book you cannot trust.
        # **Once per bar, not once per tick.** A symbol that cannot be priced
        # is worth saying — and saying it every sixty seconds buries everything
        # else. One cold start with a rate-limited feed wrote 795 identical
        # events in three minutes and pushed every real line off the page,
        # which is how a log stops being read at all.
        #
        # The state is in memory on purpose. It is presentation, not a
        # decision: losing it on restart costs one repeated line, and keeping
        # it in the ledger would mean writing to the database to decide whether
        # to write to the database.
        bar_now = resolution.bar_key(market.as_of()) or ""
        if bar_now != self._reported_bar:
            self._reported_bar = bar_now
            self._reported_blind = set()
        for symbol, why in sorted(getattr(market, "unpriceable", {}).items()):
            if symbol in self._reported_blind:
                continue
            self._reported_blind.add(symbol)
            report.bot_notes.append(f"no price for {symbol}: {why[:160]}")
            flow.emit("market", f"no price for {symbol}", kind="blocked",
                      detail=why[:300])

        # The scanners run before any firm deliberates, because a reading
        # published after the debate is a reading nobody heard. They publish a
        # score and nothing else — there is no path from src/trading/signals.py
        # to an order, so this is the one place in the tick where running a
        # stranger's file cannot even be refused, only ignored.
        report.signals = self.scanners.run(market)
        # The scribe reads the village's own dead and publishes to the same
        # board. It needs the ledger, so it cannot be a sandboxed scanner file
        # — see scribe.py. It refuses to speak on one firm's word, which today
        # means it says nothing, and says why.
        report.signals.extend(self.scribe.run(market))
        # The news desk fetches from the open internet, so it is trusted code
        # rather than a sandboxed scanner file — see news.py. Off unless
        # TRADE_NEWS_ENABLED is set, because a village that silently starts
        # making outbound requests is not one anybody asked for.
        if self.news is not None:
            report.signals.extend(self.news.run(market))
        # The shadow options desk. It writes only to `shadow_trades` and can
        # never reach the ledger, so it runs after everything that can.
        if self.shadow is not None:
            shadow = self.shadow.run(market)
            for line in shadow.opened + shadow.closed:
                report.village.append(f"shadow: {line}")
            for line in shadow.refused[:2]:
                report.bot_notes.append(f"shadow refused — {line}")
            # `notes` is where the desk explains why it could not act at all —
            # no chain, no spot, no volatility. Nothing read it, so when the
            # desk spent an hour being handed a call-only chain and refusing
            # every bar of it, the log was silent and the desk looked idle
            # rather than blocked. A desk that writes nothing has to say why.
            for line in shadow.notes[:2]:
                report.bot_notes.append(f"shadow — {line}")
            if not (shadow.opened or shadow.closed
                    or shadow.refused or shadow.notes):
                report.bot_notes.append(
                    "shadow — no reading cleared confidence on any underlying")
        for note in report.signals:
            flow.emit("market", f"scanner: {note[:70]}", detail=note[:300])

        # Expiries settle before anybody deliberates. A contract that stopped
        # existing last Friday must not be an input to this morning's debate,
        # and a firm must not be able to trade around a position it no longer
        # holds. This is also the only step in the tick that happens *to* a
        # firm rather than being decided by one.
        report.expiries = self._settle_expiries(market, flow)

        current_bar = self._bar_now(market, resolution)

        # Sentences are served in bars, and counted here because this is the
        # one place that knows the bar turned over. A firm whose time is up is
        # returned to active before it is asked for an opinion, so it can trade
        # the very bar it is released on rather than losing another one.
        from .firms import strikes as _strikes

        for record in self.store.firms():
            # **The dead do not come back on a timer.** A firm can be sent to
            # the gulag, and then be killed and wound up while its sentence is
            # still counting down. When the countdown finished, this set it
            # ACTIVE unconditionally — resurrecting a firm whose capital had
            # already been returned and whose successor had already been filed.
            # `firm_c_crypto_ii` came back that way on 2026-09-02: wound up
            # after returning $4,884.56, then active again with $54.88 of
            # allocation and no cash, ready to be evaluated and killed a second
            # time. Bankrupt is documented as the end of the estate, and the
            # only way to reverse a kill is through the court.
            if record.is_killed or record.is_bankrupt:
                continue
            served = _strikes.serve_sentence(self.store, record, current_bar)
            if served is not None and served.released:
                self.store.set_firm_status(record.id, FirmStatus.ACTIVE.value)
                report.village.append(
                    f"{record.firm_key} released from the gulag "
                    f"({served.strikes} strike(s) on record)"
                )
                flow.emit("brokerage", f"{record.firm_key} released",
                          firm=record.firm_key)

        current_bar = self._bar_now(market, resolution)
        for record in self.store.firms():
            if record.is_bankrupt:
                continue  # wound up: flat, settled, postmortem written
            if record.is_killed and not any(
                p.is_open for p in self.store.positions(record.id)
            ):
                # Dead and flat, but not yet wound up. This is where the estate
                # is closed: capital handed back, postmortem written, lesson
                # published, heir filed. Quiet and idempotent — it returns None
                # unless this is the tick that finishes the job.
                closed = bankruptcy.wind_up(self, record)
                if closed:
                    report.village.append(
                        f"{record.firm_key} wound up — returned "
                        f"{fmt_money(D(closed['returned']))}"
                        + (f", successor {closed['successor']} filed (paused, "
                           "unfunded)" if closed["successor"] else "")
                    )
                    for note in closed["diagnosis"][:2]:
                        report.village.append(f"  {record.firm_key} postmortem: {note}")
                    flow.emit("brokerage", f"{record.firm_key} wound up",
                              firm=record.firm_key, detail=closed["lesson"][:300])
                continue
            held_blind = [
                p.symbol for p in self.store.positions(record.id)
                if p.is_open and p.symbol in getattr(market, "unpriceable", {})
            ]
            if held_blind:
                report.errors.append(
                    f"{record.firm_key} holds {', '.join(held_blind)} and the feed "
                    "cannot price it — this firm's equity is not trustworthy "
                    "until that resolves"
                )
            # One deliberation per bar. The borrow charge stays outside this,
            # because it has its own per-bar guard and is a cost that accrues
            # whether or not the firm has an opinion.
            try:
                self._charge_borrow(record, market, report)
                if self._already_deliberated(record, current_bar, resolution):
                    continue
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

        # The council sits after the brokerage has produced its requests and
        # before the vault is written, so a ruling and what it caused land in
        # the same audit entry. In `human` mode both calls are no-ops and the
        # tick is byte-for-byte what it was.
        if self.config.autonomy.council_decides:
            self.request_resumes(market)
            report.rulings = self.hold_council(market)
            report.carried_out = self.apply_approvals()
            for done in report.carried_out:
                flow.move("gate", "brokerage", str(done)[:110])

        # Real money comes off the table the moment anything is wrong, and
        # needs nobody's permission to do so. This is the other half of the
        # asymmetry that makes the first half acceptable: going live takes
        # evidence and a human; coming back takes a reason and one tick.
        for firm in self.brokerage.live_firms():
            card = next((c for c in report.oversight.cards if c.firm_id == firm.id), None)
            trouble = ""
            if card is not None and not card.can_be_valued:
                trouble = "its book cannot be valued"
            elif not firm.is_active:
                trouble = f"it is {firm.status}"
            elif card is not None and D(card.drawdown_pct) > self.config.kill.max_drawdown_pct:
                trouble = f"drawdown {card.drawdown_pct}%"
            if trouble:
                done = self.brokerage.demote_firm(firm.firm_key, trouble)
                report.errors.append(
                    f"{firm.firm_key} PULLED OFF REAL MONEY: {trouble}"
                )
                # Close the loop on the decision that put it there, so the next
                # time this firm is proposed for real money the village can say
                # what happened last time rather than starting the argument
                # from nothing.
                self._settle_decision(f"put {firm.firm_key} on real money",
                                      f"pulled back: {trouble}", firm.id)
                flow.emit("brokerage", f"{firm.firm_key} back to paper: {trouble}"[:110],
                          kind="alarm", firm=firm.firm_key, detail=trouble)
                left = done.get("still_open") or []
                if left:
                    # Not liquidated on purpose — see demote_firm. This is the
                    # one thing here a person has to do by hand.
                    report.errors.append(
                        f"{firm.firm_key} STILL HOLDS {', '.join(left)} at "
                        f"{done.get('was_venue')} — nothing was sold; close it yourself"
                    )
                self.notifier.send(
                    f"🛑 {firm.firm_key} back to paper",
                    f"{firm.firm_key} was trading live and has been returned to "
                    f"paper because {trouble}. No approval was needed and none "
                    "was asked for."
                    + (f"\n\nIt still holds {', '.join(left)} at "
                       f"{done.get('was_venue')}. Nothing was sold automatically — "
                       "that is what turns an outage into a realised loss. Close "
                       "those by hand." if left else ""),
                )

        # The village learning what works. Off unless the switch is on; see
        # `run_evolution` for what it refuses to touch and why.
        report.evolved = self.run_evolution(market)
        for line in report.evolved:
            flow.emit("brain", line[:110], detail=line)

        # The arena, the bazaar and the tavern. Off unless TRADE_LIVING is set,
        # and incapable of moving cash when it is on — see src/trading/living.py.
        self.living.run(market, report)

        drawn = self.learner.lessons(report.oversight.cards)
        fresh = self.learner.new_lessons(drawn)
        report.lessons = self.learner.record(fresh)
        self.audit.log_oversight(report.oversight, market.as_of())
        self.log_brain(fresh, market)
        self.audit.rebuild_index()
        flow.move("brokerage", "audit", "vault written")
        if fresh:
            flow.move("ledger", "brain", f"{len(fresh)} lesson(s) learned",
                      detail="; ".join(str(lesson)[:80] for lesson in fresh[:3]))

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

    def _charge_borrow(self, record: FirmRecord, market: MarketData, report: TickReport) -> None:
        """Charge one *bar* of borrow on whatever this firm is short.

        Shorting is not free, and a backtest that treats it as free is a
        backtest of a strategy nobody can run. The charge is a fill with no
        quantity and a fee, which is not a trick: both reconciliation
        identities are stated in terms of fills and fees, so a cost expressed
        this way is already accounted for everywhere, and a cost expressed any
        other way would break the books.

        **Per bar, and once per bar.** This charged ``rate / 365`` every time it
        was called, and it is called once per tick — so a village ticking every
        sixty seconds billed a full day of borrow every minute, which is a year
        of financing every six hours. Nobody noticed because nothing was short.
        It is the same mistake as the Sharpe kill and the bar count: a rate
        quoted per unit of market time, charged per unit of wall-clock time.

        So the charge is prorated by how much calendar time one bar covers, and
        a bar already billed is skipped. Ticking a thousand times against one
        bar costs exactly what ticking once costs.
        """
        rate = D(self.config.firm.borrow_rate_annual)
        if rate <= 0:
            return
        shorts = [p for p in self.store.positions(record.id) if p.quantity < 0]
        if not shorts:
            return

        resolution = self.config.data.resolution
        bar = resolution.bar_key(market.as_of())
        if not bar:
            return          # no bar, no charge: see the blind-feed rule
        if self._borrow_already_billed(record, bar, resolution):
            return
        # Ask the data how long a bar is; fall back to the configured
        # resolution only when it cannot say. The config describes the live
        # loop and the backtest feed does not have to agree with it — when it
        # didn't, borrow came out 96x too small and rounded away to nothing.
        per_bar = market.bar_span_days() or resolution.calendar_days

        from .models import Fill, Side

        for position in shorts:
            try:
                mark = D(market.mark(position.symbol))
            except Exception:  # noqa: BLE001 - no mark, no charge this tick
                continue
            fee = money(abs(position.quantity) * mark * rate / D(365) * per_bar)
            if fee <= 0:
                continue
            # A firm that cannot pay its borrow is in trouble, but overdrawing
            # it is not how this system says so — the kill switch is.
            if money(record.cash - fee) < 0:
                report.errors.append(
                    f"{record.firm_key}: cannot pay {fmt_money(fee)} borrow on "
                    f"{position.symbol}"
                )
                continue
            self.store.settle(record, Fill(
                firm_id=record.id,
                symbol=position.symbol,
                side=Side.BUY.value,
                quantity=ZERO,
                price=ZERO,
                fee=fee,
                venue=record.venue,
                as_of=market.as_of(),
            ))
            self.flow.emit("ledger", f"borrow on {position.symbol}", firm=record.firm_key,
                           detail=f"{fmt_money(fee)} at {rate * 100}% a year")

    def _remember_decision(self, what: str, why: str, firm_key: str = "",
                           payload: Optional[dict] = None) -> None:
        """Note what the village chose and why, before the result is known.

        Best-effort by design: a village that cannot write a diary entry still
        has to be able to kill a firm. Nothing here may raise into a decision.
        """
        try:
            record = self.store.get_firm(firm_key) if firm_key else None
            self.memory.remember_decision(
                what, why, firm_id=getattr(record, "id", None), payload=payload)
        except Exception:  # noqa: BLE001 - a diary is never worth a tick
            pass

    def _settle_decision(self, what: str, outcome: str,
                         firm_id: Optional[int] = None) -> None:
        """Record how the most recent decision of this kind turned out."""
        try:
            for memory in self.memory.precedent(what, firm_id=firm_id, limit=1):
                self.memory.settle_decision(memory.id, outcome)
        except Exception:  # noqa: BLE001
            pass

    def _costed_for(self, record: FirmRecord) -> TradingConfig:
        """The village config, with this firm's own trading costs in it.

        Read from the firm's spec rather than the database because costs are a
        property of the venue it trades, which is configuration, not ledger.
        Firms with nothing declared get the village default and the same object
        back, so the common case allocates nothing.
        """
        spec = self._specs.get(record.firm_key)
        if spec is None or not getattr(spec, "costs_overridden", False):
            return self.config
        import dataclasses

        return dataclasses.replace(self.config, data=spec.costed(self.config.data))

    def _already_deliberated(self, record: FirmRecord, bar: str, resolution) -> bool:
        """Has this firm already had its say on this bar?

        **A decision belongs to a bar, not to a tick.** The loop runs every
        sixty seconds and the bar changes every hour, so without this a firm
        re-derives the same conclusion from the same prices up to sixty times
        and acts on it every time. It is not sixty decisions. It is one
        decision, executed until the money runs out.

        That is not hypothetical. 634 of the village's first 666 fills were
        repeats of the same firm, symbol, side and bar. On 28 August the
        commodities desk decided to buy USO once and bought it seven times in
        nine minutes — a $2,101 position became $13,867, 80% of the firm's cash
        went into one name, and the last fill was partial because there was
        nothing left. The risk limit said 3% of allocation. It ended up at a
        fifth of the book, and no limit was breached, because each of the seven
        was individually within every rule.

        It is also what made the costs real: three of the five bankruptcy
        postmortems blame fees rather than direction, and one of them put
        fees at 1144% of everything the winning trades made. Sixty times the
        turnover is sixty times the cost of being right.

        Read from the ledger rather than kept in memory, for the same reason
        the borrow charge is: the loop gets restarted, and a guard that forgets
        on restart is a guard that stops working exactly when someone is
        watching. Proposals are written whatever the risk manager says, so a
        firm that was blocked this bar has still had its say and does not get
        to ask again — the blocked ones were most of the churn.
        """
        if not bar:
            return False        # no bar: the blind-feed rule handles it
        try:
            rows = self.db.query(
                "SELECT as_of FROM trade_proposals WHERE firm_id = ? "
                "ORDER BY id DESC LIMIT 12",
                (record.id,),
            )
        except Exception:  # noqa: BLE001 - never fail a tick over this check
            return False
        return any(resolution.bar_key(r.get("as_of")) == bar for r in rows)

    def _bar_now(self, market: MarketData, resolution) -> str:
        """The current bar, never earlier than the last one this run saw.

        `market.as_of()` is `max(bar.as_of)` across symbols and can go
        *backwards* between ticks: a partial bar shows up in one fetch and is
        missing from the next, so the maximum drops back to the previous one.
        Observed live alternating 16:00 / 16:15 within the same minute.

        Every guard in this file that asks "did I already do this bar?" tests
        equality against a stored key, and equality cannot survive a value that
        oscillates — each flip reads as a fresh bar. That is not theoretical:
        it served an 80-bar gulag sentence in two hours, and it bills borrow
        once per alternating key instead of once per bar.

        Clamping here rather than in `MarketData` on purpose. `as_of()` is
        honestly reporting what the feed returned, and a backtest that seeks
        backwards through history *should* see the bar go down. It is only the
        live tick, moving forward in wall-clock time, where a bar going
        backwards is always wrong.
        """
        bar = resolution.bar_key(market.as_of())
        if not bar:
            return bar
        if bar < self._high_bar:
            return self._high_bar
        self._high_bar = bar
        return bar

    def _borrow_already_billed(self, record: FirmRecord, bar: str, resolution) -> bool:
        """Has this firm already paid borrow for this bar?

        Read from the ledger rather than kept in memory, because the tick loop
        is restarted routinely and a charge that resets on restart is a charge
        that scales with how often you deploy.
        """
        try:
            rows = self.db.query(
                "SELECT as_of FROM fills WHERE firm_id = ? AND quantity = '0' "
                "ORDER BY id DESC LIMIT 8",
                (record.id,),
            )
        except Exception:  # noqa: BLE001 - never fail a tick over a fee check
            return False
        return any(resolution.bar_key(r.get("as_of")) == bar for r in rows)

    def _run_firm(self, record: FirmRecord, market: MarketData, report: TickReport) -> None:
        firm = self.build_firm(record)
        market.register(record.universe)
        positions = self.store.positions(record.id)
        # A venue costed for *this* firm. Coinbase charges 0.6% a side and an
        # ETF desk 0.05%; one number for both makes the crypto firms look ten
        # times better than they are, and for a strategy whose deficit is
        # smaller than its fees that is the whole result rather than a detail.
        venue = build_venue(record.venue, self._costed_for(record), self.gate)

        flow = self.flow
        raw = firm.propose(market, positions)

        # A bot that returned nothing usable looks exactly like a quiet day.
        # Say which it was, or the first thing you do when a firm stops trading
        # is go looking in the wrong place.
        for complaint in getattr(firm, "bot_complaints", []):
            report.bot_notes.append(f"{record.firm_key}: {complaint}")
            flow.emit("firms", f"{record.firm_key}'s bot: {complaint[:70]}",
                      kind="blocked", firm=record.firm_key, detail=complaint)

        for proposal in raw:
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

            # Stamp the feed before settling: `store.settle` records which
            # feed built the position, and it is the only place with both the
            # fill and the transaction open.
            fill.priced_by = getattr(self.feed, "name", "") or ""
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
    # =====================================================================
    # the council
    #
    # The gate is unchanged: every one of these decisions is still a row in
    # `human_approvals`, still granted through `HumanGate.approve`, still
    # carried out by `apply_approvals`. The only thing that moves is *who*
    # signs the row. That is deliberate — a second, quieter path into the
    # ledger for autonomous decisions is exactly the kind of thing that makes
    # an audit trail worthless.
    # =====================================================================
    def hold_council(self, market: Optional[MarketData] = None) -> list:
        """Rule on everything pending that the council is allowed to rule on."""
        if not self.config.autonomy.council_decides:
            return []

        market = market or self.market()
        rulings = []
        for approval in self.gate.pending():
            if approval.action == ApprovalAction.LIVE_TRADING.value:
                continue  # never the council's; see src/trading/council
            evidence = gather_evidence(self, approval, market)
            if self.council.panel_for(evidence) is None:
                continue  # not a council matter at all; it stays with a human
            if self.council.already_heard(approval.id, evidence):
                continue  # same request, same facts — the ruling stands
            ruling = self.council.rule(evidence)
            self.council.record(ruling, approval.id)
            rulings.append(ruling)

            if ruling.granted:
                self.gate.approve(approval.id, approved_by="the council")
                self.flow.move("gate", "brokerage", f"council granted: {ruling.reason[:70]}",
                               firm=ruling.firm_key, detail=str(ruling))
            elif ruling.verdict == "refuse":
                self.gate.reject(approval.id, approved_by="the council")
                self.flow.emit("gate", f"council refused: {ruling.reason[:70]}",
                               kind="blocked", firm=ruling.firm_key, detail=str(ruling))
            else:
                # The one outcome that needs a person, so this is the one that
                # is allowed to interrupt them.
                self.gate.notify_request(approval)
                self.flow.emit("gate", f"council deferred to you: {ruling.reason[:70]}",
                               kind="blocked", firm=ruling.firm_key, detail=str(ruling))
        return rulings

    def request_resumes(self, market: Optional[MarketData] = None) -> list:
        """Ask for paused firms whose condition has cleared to trade again.

        Pausing is autonomous because it stops the bleeding. Starting again is
        the other direction, so it goes through the gate like everything else —
        and without this the village only ever empties out.
        """
        from .firms.kill_switch import INSUFFICIENT_DATA, should_kill_firm

        market = market or self.market()
        asked = []
        for firm in self.store.firms():
            if firm.status != FirmStatus.PAUSED.value:
                continue
            card = self.brokerage.evaluator.evaluate(firm, market)
            if card is None:
                continue
            should, reason = should_kill_firm(card.to_metrics(), self.config.kill)
            if should and reason != INSUFFICIENT_DATA:
                continue  # still bleeding; it stays paused
            approval = self.gate.request(
                ApprovalAction.RESUME_FIRM.value,
                f"Resume {firm.firm_key}: the condition that paused it has cleared",
                details={
                    "kind": "resume",
                    "firm": firm.firm_key,
                    "reason": "no kill condition currently met",
                },
                dedupe_key=f"resume:{firm.firm_key}",
            )
            asked.append(approval)
        return asked

    # =====================================================================
    # the brain, in the vault
    #
    # The database is where memory is kept; the vault is where it can be
    # *read*. Every link written here is a relationship already in the data —
    # a lesson to the symbol it is about, a symbol to the firms that traded
    # it, a genome to the genome it was mutated from — which is what makes a
    # graph view over this vault worth opening.
    #
    # Best-effort, like the flow recorder: a tick does not fail because a note
    # could not be written.
    # =====================================================================
    def log_brain(self, lessons: Sequence = (), market: Optional[MarketData] = None) -> dict:
        """Write what the village has learned into the vault."""
        written = {"lessons": 0, "symbols": 0, "genomes": 0}
        try:
            firms = self.store.firms()
            for lesson in lessons:
                self.audit.log_lesson(lesson, firm_key=_firm_for(lesson, firms))
                written["lessons"] += 1

            traded_by: dict = {}
            for firm in firms:
                for symbol in firm.universe or ():
                    traded_by.setdefault(symbol.upper(), []).append(firm.firm_key)

            keys_by_symbol = self._lesson_keys_by_symbol()
            for symbol, keys in traded_by.items():
                record = self.memory.track_record(symbol)
                if not record.get("trades") and symbol not in keys_by_symbol:
                    continue  # nothing has happened in this name yet
                self.audit.write_symbol(record, firms=keys,
                                        lesson_keys=keys_by_symbol.get(symbol, ()))
                written["symbols"] += 1

            by_id = {f.id: f.firm_key for f in firms}
            for row in self.db.query(
                "SELECT * FROM strategy_genomes ORDER BY id DESC LIMIT 200"
            ):
                if self.audit.write_genome(row, by_id.get(row.get("firm_id"), "")):
                    written["genomes"] += 1

            self.audit.rebuild_brain()
        except Exception as exc:  # noqa: BLE001 - the vault never breaks a tick
            self.flow.emit("brain", "vault note failed", kind="blocked", detail=str(exc)[:200])
        return written

    def _lesson_keys_by_symbol(self) -> dict:
        out: dict = {}
        for memory in self.memory.lessons(limit=500):
            symbol = (memory.symbol or "").upper()
            key = memory.payload.get("key")
            if symbol and key:
                out.setdefault(symbol, []).append(key)
        return out

    def recruit(self, path, submitted_by: str = "", stake=None, name: str = ""):
        """Drop a bot file in; the court rules, and a cleared file becomes a firm."""
        from .recruit import recruit as _recruit

        return _recruit(self, path, submitted_by=submitted_by, stake=stake, name=name)

    def recruit_all(self, directory, submitted_by: str = "", move_to=None) -> list:
        from .recruit import watch as _watch

        return _watch(self, directory, submitted_by=submitted_by, move_to=move_to)

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
                self._remember_decision(
                    f"killed {result['firm']}", result.get("reason", ""),
                    firm_key=result["firm"], payload=result)
            elif (approval.action == ApprovalAction.LIVE_TRADING.value
                    and details.get("firm")):
                # A per-firm promotion to real money, distinguished from the
                # venue-level request by carrying a firm in its details. The
                # council cannot reach this: it has no panel for LIVE_TRADING,
                # so the only way a row gets here is a human at the gate.
                try:
                    done = self.brokerage.promote_firm(
                        details["firm"], details.get("venue", "alpaca"),
                        details.get("capital", "0"), approval.approved_by or "human",
                    )
                except (ValueError, TradingLedgerError) as exc:
                    # The approval was for a firm as it stood when the request
                    # was filed. If it has moved since, the answer is to refuse
                    # and leave the row unapplied — never to go live anyway.
                    applied.append(f"REFUSED going live for {details['firm']}: {exc}")
                    self.flow.emit("gate", f"live promotion refused: {exc}"[:110],
                                   kind="blocked", firm=details["firm"], detail=str(exc))
                    continue
                applied.append(
                    f"{done['firm']} is LIVE on {done['venue']} with "
                    f"{fmt_money(done['capital'])}"
                )
                self._remember_decision(
                    f"put {done['firm']} on real money",
                    f"approved by {approval.approved_by or 'human'} at "
                    f"{fmt_money(done['capital'])} on {done['venue']}",
                    firm_key=done["firm"], payload=done)
                self.notifier.send(
                    f"🟢 {done['firm']} is trading REAL MONEY",
                    f"{done['firm']} was approved for {done['venue']} with "
                    f"{fmt_money(done['capital'])}. It comes back to paper "
                    "automatically the moment anything is wrong with it.",
                )
            elif approval.action == ApprovalAction.RESUME_FIRM.value and details.get("firm"):
                result = self.brokerage.resume_firm(
                    details["firm"], approval.approved_by or "the council"
                )
                applied.append(f"resumed {result['firm']}")
                self._remember_decision(
                    f"resumed {result['firm']}",
                    f"approved by {approval.approved_by or 'the council'}",
                    firm_key=result["firm"], payload=result)
            elif approval.action == ApprovalAction.ALLOCATE_CAPITAL.value and details.get("firm"):
                if details.get("kind") == "capital_transfer":
                    continue  # settled through the black market, not here
                if details.get("kind") == "recruit":
                    from .recruit import fund

                    result = fund(
                        self, details["firm"], D(details["new_allocation"]),
                        by=approval.approved_by or "approval",
                    )
                    applied.append(
                        f"funded {result['firm']} with {result['allocation']}"
                    )
                    details["applied"] = True
                    self.db.update("human_approvals", approval.id,
                                   {"details": json.dumps(details, default=str)})
                    continue
                # The authoriser travels with the money. Every other branch in
                # this method already passes `approval.approved_by`; this one
                # did not, so the ledger fell back to the default reason and an
                # empty `by` — and wrote the words "approved by human" against
                # six raises that took firm_b_stocks from $100,000 to $177,156
                # on the council's authority, with no human anywhere near them.
                # A raise is the one direction the system is never allowed to
                # take by itself, so of every field in this ledger, this is the
                # one that must not be able to say something untrue.
                change = self.brokerage.allocator.apply_approved_increase(
                    details["firm"], D(details["new_allocation"]),
                    reason=approval.reason or "approved increase",
                    by=approval.approved_by or "",
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

    def run_evolution(self, market: MarketData) -> list:
        """Let the village improve its own strategies, on its own, on a cadence.

        This is the thing the brain, the court and the jury were built for: the
        firms are not supposed to run the genome they were born with forever.
        It is off until the `evolution` switch is on, and then it runs every
        `TRADE_EVOLVE_EVERY` **market bars** — bars, never ticks, for the
        reasons that have their own module.

        **What it will not touch, and why each one matters.**

        *A firm on a live venue.* Changing what a strategy does while it is
        trading real money, with nobody asked, is the one thing the whole
        promotion gate exists to prevent. A firm that has been through that
        gate was approved on the evidence of a specific genome; swapping it
        underneath would make the approval meaningless. Take it back to paper
        first and it evolves like everything else.

        *A killed firm.* Killed is terminal. Evolving a corpse is not a
        resurrection, it is a waste of a backtest.

        *A blind or unreconciled village.* Both are the standing rule: a
        decision made from numbers the system knows are missing is worse than
        no decision. `evolve` already refuses on a broken ledger; this refuses
        earlier and more quietly, because a tick should not raise over an
        optional improvement.

        Everything it does is a *proposal* recorded in `strategy_genomes`, and
        the adoption test is a held-out window neither candidate was chosen on
        — see `Evolver.evolve`. It moves no capital, sends no order and asks
        for no approval, because it changes what a firm would do rather than
        what it may risk.
        """
        if not self.settings.get("evolution", default=False):
            return []
        every = int(self.config.brain.evolve_every or 0)
        if every <= 0:
            return []
        resolution = self.config.data.resolution

        # **Bars observed, not calendar days.** This asked `living._clock`,
        # which returns `stamp.date().toordinal()` — a day number, as its own
        # docstring says. So a cadence documented three lines above as "every
        # TRADE_EVOLVE_EVERY *market bars* — bars, never ticks" was in fact
        # every twenty calendar *days*: a factor of six and a half out on an
        # hourly feed, and the eighth outing for the unit confusion this file
        # keeps promising is the last one.
        #
        # What it cost is the whole point of the module. Every firm in the
        # village had zero generations on record after eighteen days alive —
        # the brain, the court and the jury were built so firms would not run
        # the genome they were born with forever, and every firm had run
        # exactly that, because the next qualifying day never arrived.
        stamp = market.as_of()
        if stamp is None:
            return []
        # The bar's own number: how many bar-lengths it sits from the epoch.
        # Derived from the bar rather than counted in a table, which keeps the
        # property the old code was right about — replay the same window twice
        # and the same generations happen on the same bars — while fixing the
        # unit it was wrong about. On a daily resolution this is the day
        # number the original returned, so a daily village is unaffected.
        seen = int(stamp.timestamp() // int(resolution.seconds))
        if seen % every != 0:
            return []

        # Once per bar, not once per tick inside a qualifying bar. `day % every`
        # stays true for the whole of that bar, and the loop comes round every
        # sixty seconds — so without this, a daily village would sweep a whole
        # generation every minute for a day. That is this repository's oldest
        # mistake and it does not get a fifth outing.
        bar = self.config.data.resolution.bar_key(market.as_of())
        if not bar or self._already_evolved(bar):
            return []
        if not self.brokerage.reconcile(market).ok:
            return ["evolution skipped: the books do not reconcile"]
        self.store.record_event(
            "evolution_swept", f"a generation ran on bar {bar}", payload={"bar": bar}
        )

        out: list = []
        for record in self.store.firms():
            if record.is_killed:
                continue
            if record.venue != "paper":
                out.append(
                    f"{record.firm_key} left alone: it is on {record.venue} with "
                    "real money, and its genome was what you approved"
                )
                continue
            try:
                spec = self._specs.get(record.firm_key)
                analysts = list(getattr(spec, "analysts", None) or
                                ("technical", "sentiment", "macro"))
                generation = self.store.next_generation(record.id)
                gen = self.evolver.evolve(record, market, generation, analysts)
            except Exception as exc:  # noqa: BLE001 - never fail a tick to learn
                out.append(f"{record.firm_key} could not evolve: {exc}")
                continue
            if gen.promoted:
                out.append(
                    f"{record.firm_key} ADOPTED a new genome at generation "
                    f"{gen.number} — it won the fit and the held-out bars"
                )
                self._remember_decision(
                    f"adopted a genome for {record.firm_key}",
                    f"generation {gen.number}: won the fit and the held-out bars",
                    firm_key=record.firm_key,
                    payload={"generation": gen.number})
            elif gen.refused:
                out.append(f"{record.firm_key} held: {gen.refused}")
        return out

    def _settle_expiries(self, market: MarketData, flow) -> list:
        """Close every contract that has reached its expiry date.

        Wrapped so an expiry cannot take the tick down with it. An option that
        fails to settle leaves a position the firm cannot value, which the
        `CANNOT_VALUE` refusal already handles correctly — whereas an
        exception here would stop every other firm from trading too.
        """
        from . import expiry

        notes: list = []
        try:
            report = expiry.settle_all(
                self.store, self.store.firms(), market, market.as_of()
            )
        except Exception as exc:  # noqa: BLE001 - one bad contract is not a tick
            note = f"expiry settlement failed: {exc}"
            flow.emit("market", note[:70], kind="blocked", detail=note[:300])
            return [note]

        for done in report.settled:
            notes.append(str(done))
            flow.emit("market", f"expired: {done.symbol}", detail=str(done)[:300])
        for refused in report.refused:
            notes.append(str(refused))
            flow.emit("market", f"cannot settle {refused.symbol}",
                      kind="blocked", detail=str(refused)[:300])
        return notes

    def _already_evolved(self, bar: str) -> bool:
        """Has a generation already run on this market bar?

        Read from the event log rather than held in memory, because the tick
        loop is restarted routinely and a guard that resets on restart is a
        guard that scales with how often you deploy.
        """
        try:
            rows = self.db.query(
                "SELECT payload FROM brokerage_events WHERE event_type = ? "
                "ORDER BY id DESC LIMIT 4",
                ("evolution_swept",),
            )
        except Exception:  # noqa: BLE001 - never fail a tick over a guard
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
        if out:
            # New genomes exist; the lineage in the vault should show them.
            self.log_brain()
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
            # Firms whose equity is arithmetic rather than measurement. The
            # total above still includes them, because leaving them out would
            # be a different lie — but a caller that prints the total without
            # printing this is presenting fiction as a number.
            "unmeasured": [c.firm_key for c in cards if not c.can_be_valued],
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


def _firm_for(lesson, firms) -> str:
    """Which firm a lesson is about, when it is about one at all."""
    topic = getattr(lesson, "topic", "") or ""
    return next((f.firm_key for f in firms if f.firm_key == topic), "")


__all__ = ["Ecosystem", "TickReport"]
