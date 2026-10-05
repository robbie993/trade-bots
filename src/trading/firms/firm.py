"""The Firm — one trading pod.

Assembles the seats the build document lists and runs them in the order a
real desk does:

    analysts → bull/bear debate → trader (sizing) → risk manager

and stops there. A firm produces *proposals*; it never executes. Execution
needs the conscience, the venue and the ledger, which live outside the pod
and are wired together in ``ecosystem.py``. Keeping the pod one step short of
the money is what lets the backtester, the paper loop and a live loop all
reuse it unchanged.

The firm may pause itself at any time. It cannot un-pause itself, and it
cannot change its own allocation — both of those are the brokerage's, and the
brokerage needs a human for the half that increases risk.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ...money import D, ZERO, money, percent
from ..config import FirmDefaults, FirmKillConfig, TradingConfig
from ..data.market_data import MarketData
from .. import adapter
from ..models import (
    FirmRecord, FirmStatus, Position, RiskVerdict, Side, Signal, TradeProposal, price, qty,
)
from .analysts import build_analysts
from .kill_switch import FirmMetrics, KillSwitch
from .researchers import DebateResult, DebateRoom
from .risk_manager import RiskDecision, RiskManager
from .spec import FirmSpec
from .trader import Trader


class Firm:
    def __init__(
        self,
        record: FirmRecord,
        analysts: Optional[Sequence] = None,
        limits: Optional[FirmDefaults] = None,
        kill_config: Optional[FirmKillConfig] = None,
    ):
        self.record = record
        self.limits = limits or FirmDefaults()
        self.analysts = list(analysts if analysts is not None else build_analysts(("technical",)))
        self.debate_room = DebateRoom()
        self.trader = Trader(self.limits)
        self.risk_manager = RiskManager(self.limits)
        self.kill_switch = KillSwitch(kill_config or FirmKillConfig())
        # What the firm's bot got wrong this tick, if it runs one. Set here
        # rather than in `_from_bot` so a pod firm still answers the question.
        self.bot_complaints: list = []
        # What the exit rules must remember between bars: the best price each
        # holding has reached and which scale-outs it has already taken. A
        # backtest keeps one Firm for the whole replay; the live loop builds a
        # new Firm every tick, so the ecosystem hands each firm the same dict
        # every time (`Ecosystem.build_firm`). The dict dies with the worker,
        # and every deploy restarts it, so `recall_exit` rebuilds a holding's
        # memory from the ledger and the bars when the dict has none for it.
        self.exit_memory: dict = {}
        #: ``(symbol, market) -> {"peak": Decimal, "scaled": bool}``, or None
        #: where there is no ledger to ask (a backtest keeps one Firm for the
        #: whole replay, so it never forgets).
        self.recall_exit = None
        #: ``() -> Decimal or None``: the firm's equity when the UTC day began,
        #: for the daily loss halt. None where there is no ledger to ask.
        self.day_open_equity = None
        #: ``() -> int``: buy fills since the UTC day began, for the trade cap.
        #: None where there is no ledger to ask.
        self.opens_today = None
        #: Fraction of a position's value one round trip costs here (fees and
        #: spread, in and out). None where nobody has said, which turns the
        #: edge-versus-costs rule off.
        self.round_trip_cost = None

    # -- construction -----------------------------------------------------
    @classmethod
    def from_spec(
        cls,
        spec: FirmSpec,
        record: FirmRecord,
        config: Optional[TradingConfig] = None,
        board=None,
    ) -> "Firm":
        cfg = config or TradingConfig()
        return cls(
            record=record,
            analysts=build_analysts(spec.analysts, board=board),
            limits=cfg.firm,
            kill_config=cfg.kill,
        )

    @property
    def key(self) -> str:
        return self.record.firm_key

    @property
    def universe(self) -> list:
        return list(self.record.universe)

    @property
    def genome(self) -> dict:
        return dict(self.record.genome or {})

    # -- deliberation -----------------------------------------------------
    def analyse(self, symbol: str, market: MarketData) -> list[Signal]:
        genome = self.genome
        return [analyst.analyse(symbol, market, genome) for analyst in self.analysts]

    def deliberate(self, symbol: str, market: MarketData) -> tuple[DebateResult, list[Signal]]:
        signals = self.analyse(symbol, market)
        return self.debate_room.debate(symbol, signals), signals

    def equity(self, market: MarketData, positions: Sequence[Position]) -> Decimal:
        """Cash plus mark-to-market. The number every limit is measured against."""
        held = sum(
            (p.market_value(market.mark(p.symbol)) for p in positions if p.is_open), ZERO
        )
        return money(self.record.cash + held)

    def propose(
        self, market: MarketData, positions: Sequence[Position]
    ) -> list[TradeProposal]:
        """Run the whole pod over the firm's universe.

        Returns risk-reviewed proposals, including ones the risk manager
        blocked: a blocked proposal is written to the database too, so the
        record shows what the firm wanted to do and why it did not.
        """
        if self.record.status == FirmStatus.BANKRUPT.value:
            return []           # wound up: flat, settled, nothing left to do

        if self.record.status == FirmStatus.KILLED.value:
            # **A dead firm sells, and does nothing else.**
            #
            # This used to `return []`, which read as "the dead do not trade"
            # and was the opposite of what the rest of the system promised.
            # `brokerage.kill_firm` says in as many words that open positions
            # are not liquidated there because "a killed firm keeps exiting on
            # the next tick and hands back cash as it does". It never could:
            # this line stopped it before it proposed anything.
            #
            # So six firms died holding $155,859 of live market exposure, with
            # no path to sell any of it, for as long as thirteen days. Killing
            # a firm stopped it opening risk and froze the risk it already had.
            # That is not stopping the bleeding; it is walking away from it.
            #
            # Selling to flat is the one action that is always allowed — the
            # risk manager exempts it by construction ("reducing an open
            # position") and the conscience was taught not to block the way
            # out. So the estate proposes exits, every tick, until there is
            # nothing left to sell, and then `bankruptcy.wind_up` closes it.
            return self._review_all(
                self._liquidation(market, positions, market.as_of()),
                market, positions, self.equity(market, positions),
            )

        equity = self.equity(market, positions)
        as_of = market.as_of()
        proposals: list[TradeProposal] = []

        # The stop runs first and outside the debate, because a position past
        # its stop is not a question the analysts get a vote on. This is the
        # house rule at position scale: the system may always stop the
        # bleeding. Without it a losing trade rides until the bear case wins
        # the argument — which, for a trend-follower, is long after the trend
        # broke, and is exactly how a village ends up with an average loss
        # eight times its average win.
        stopped = self._stops(market, positions, as_of)
        stopped_symbols = {p.symbol for p in stopped}
        taken, riding = self._take_profits(
            market, [p for p in positions if p.symbol not in stopped_symbols], as_of)
        exits = list(stopped) + list(taken)
        opinions = list(
            self._from_bot(market, positions, equity, as_of)
            if adapter.is_bot(self.record.strategy or "")
            else self._from_pod(market, positions, as_of)
        )
        # A symbol the stop or a take-profit is closing is not also a symbol to
        # trade on opinion this bar. Closing wins. And a winner the firm has
        # chosen to ride is not sold on the analysts' say-so while it rides:
        # only an exit rule, or the stop, takes it off.
        closing = {p.symbol for p in exits}
        raw = exits + [
            p for p in opinions
            if p.symbol not in closing
            and not (p.symbol in riding and p.side == Side.SELL.value)
        ]

        return self._review_all(raw, market, positions, equity)

    # -- taking profit ------------------------------------------------------
    #: The exit genes. Every one defaults to zero, which is off, so a genome
    #: that does not name them trades exactly as it did before they existed.
    #: They are read here and nowhere else. Deliberately not in the evolver's
    #: GENES table yet: they are hand-set per variant until a held-out test
    #: says which, if any, are worth letting evolution move.
    EXIT_GENES = ("tp_pct", "tp_scale_pct", "tp_scale_frac", "tp_scale2_pct",
                  "trail_arm_pct", "trail_pct", "tp_atr", "atr_window",
                  "ride_pct", "breakeven_arm_pct")

    def _take_profits(self, market: MarketData, positions: Sequence[Position], as_of) -> tuple:
        """Exits on a winning position, and which winners to ride.

        Until these existed a firm had one way out of a winner: the analysts
        changing their minds, often at the first wobble, so the average win
        stayed small. Each rule below is a different answer to "when is this
        winner done", all measured from what the position cost:

        * ``tp_pct`` — a fixed target: gain of this many percent, sell it all.
        * ``tp_scale_pct`` (+ ``tp_scale_frac``, ``tp_scale2_pct``) — sell a
          fraction at the first target and let the rest run to a second one
          (or to the trail, or to the analysts).
        * ``trail_arm_pct`` + ``trail_pct`` — once up ``arm``, sell it all if
          it gives back ``trail`` percent from the best price since.
        * ``tp_atr`` — a target that scales with the name: entry plus this many
          average bar ranges (``atr_window`` bars, 14 by default).
        * ``breakeven_arm_pct`` — once up this far, never let it become a loss:
          sell if it comes back to what it cost.
        * ``ride_pct`` — while up at least this much, the analysts' sell votes
          are ignored; only the rules above (or the stop) close it.

        Returns ``(proposals, riding)``, where ``riding`` is the set of symbols
        whose analyst sells are to be suppressed this bar.
        """
        g = self.record.genome or {}
        gene = {name: self._gene_value(g, name, 0) for name in self.EXIT_GENES}
        if not any(gene[n] > 0 for n in self.EXIT_GENES if n not in ("tp_scale_frac", "atr_window")):
            return [], set()
        memory = self.exit_memory
        out, riding = [], set()
        held_now = set()
        for held in positions:
            if not held.is_open or held.quantity <= 0:
                continue                        # the firm does not short
            entry = D(held.avg_price)
            if entry <= 0 or held.symbol in getattr(market, "unpriceable", {}):
                continue
            mark = price(market.mark(held.symbol))
            if mark <= 0 or market.bar(held.symbol) is None:
                continue
            held_now.add(held.symbol)
            state = memory.setdefault(held.symbol, {})
            if state.get("entry") != str(entry):
                # A new position, or one averaged into: start its memory over.
                state.clear()
                state["entry"] = str(entry)
                # Or a worker that restarted and forgot it. Starting the peak
                # at today's price disarmed every breakeven: a winner that had
                # run 1% and come back to +0.2% before a deploy was free to
                # become a loss after it.
                recalled = self.recall_exit(held.symbol, market) if self.recall_exit else None
                if recalled:
                    if recalled.get("peak") is not None:
                        state["peak"] = str(recalled["peak"])
                    if recalled.get("scaled"):
                        state["scaled"] = True
            peak = max(D(state.get("peak", mark)), mark)
            state["peak"] = str(peak)
            gain = (mark - entry) / entry * D(100)
            best = (peak - entry) / entry * D(100)

            why, fraction = self._exit_reason(market, held.symbol, gene, state,
                                              entry, mark, peak, gain, best)
            if why:
                quantity = qty(held.quantity * fraction)
                if quantity <= 0:
                    continue
                out.append(TradeProposal(
                    firm_id=self.record.id,
                    symbol=held.symbol,
                    side=Side.SELL.value,
                    quantity=quantity,
                    confidence=D(100),
                    reference_price=mark,
                    rationale=f"TAKE PROFIT: {held.symbol} {why} (in at {entry}, now {mark}).",
                    as_of=as_of,
                ))
            elif gene["ride_pct"] > 0 and gain >= gene["ride_pct"]:
                riding.add(held.symbol)
        for symbol in list(memory):
            if symbol not in held_now:
                del memory[symbol]              # flat: nothing left to remember
        return out, riding

    def _exit_reason(self, market, symbol, gene, state, entry, mark, peak, gain, best) -> tuple:
        """(why, fraction to sell) for one winning position, or ("", 0)."""
        one = D(1)
        if gene["tp_pct"] > 0 and gain >= gene["tp_pct"]:
            return f"is up {percent(gain)}%, past its {gene['tp_pct']}% target", one
        if gene["tp_atr"] > 0:
            window = int(gene["atr_window"]) or 14
            bars = market.history(symbol, window)
            rng = None
            if len(bars) >= window:
                from ..indicators import average_range
                rng = average_range([b.high for b in bars], [b.low for b in bars], window)
            if rng and mark >= entry + gene["tp_atr"] * rng:
                return f"reached entry + {gene['tp_atr']} average ranges", one
        if gene["trail_arm_pct"] > 0 and gene["trail_pct"] > 0 and best >= gene["trail_arm_pct"]:
            floor = peak * (one - gene["trail_pct"] / D(100))
            if mark <= floor:
                return (f"gave back {gene['trail_pct']}% from its best ({peak}) after "
                        f"running {percent(best)}%"), one
        if gene["breakeven_arm_pct"] > 0 and best >= gene["breakeven_arm_pct"] and gain <= 0:
            return f"came back to cost after running {percent(best)}%", one
        if gene["tp_scale_pct"] > 0:
            frac = gene["tp_scale_frac"] if gene["tp_scale_frac"] > 0 else D("0.5")
            if not state.get("scaled") and gain >= gene["tp_scale_pct"]:
                state["scaled"] = True
                return f"is up {percent(gain)}%: first target, selling {frac * 100}%", min(frac, one)
            if (state.get("scaled") and gene["tp_scale2_pct"] > 0
                    and gain >= gene["tp_scale2_pct"]):
                return f"is up {percent(gain)}%, past its second target", one
        return "", ZERO

    def _review_all(self, raw, market, positions, equity) -> list[TradeProposal]:
        """Put every proposal past the risk manager, keeping what survives."""
        out: list[TradeProposal] = []
        for proposal in raw:
            reference = proposal.reference_price or price(market.mark(proposal.symbol))
            if proposal.side_enum.sign > 0 and proposal.quantity > 0:
                why_not = self._lessons(proposal, market)
                if why_not:
                    proposal.risk_verdict = RiskVerdict.BLOCK.value
                    proposal.risk_reason = why_not
                    proposal.quantity = ZERO
                    proposal.notional = ZERO
                    proposal.status = "rejected"
                    out.append(proposal)
                    continue
            reviewed = self._review(proposal, reference, positions, equity)
            if reviewed is not None:
                out.append(reviewed)
        return out

    def _liquidation(self, market, positions, as_of) -> list[TradeProposal]:
        """Sell the whole book. The estate's only remaining opinion.

        Whole positions, not fractions: a dead firm is not managing an exit, it
        is ending one. A symbol the feed cannot price is skipped rather than
        dumped at a guess — an unpriceable holding stays on the books and keeps
        the firm in liquidation, which is the honest outcome. It is better to
        stay unfinished and say so than to book a settlement at a made-up mark.
        """
        blind = getattr(market, "unpriceable", {})
        out: list[TradeProposal] = []
        for held in positions:
            if not held.is_open or held.symbol in blind:
                continue
            mark = price(market.mark(held.symbol))
            if mark <= 0 or market.bar(held.symbol) is None:
                continue
            out.append(TradeProposal(
                firm_id=self.record.id,
                symbol=held.symbol,
                side=(Side.SELL if held.quantity > 0 else Side.BUY).value,
                quantity=qty(abs(held.quantity)),
                confidence=D(100),
                reference_price=mark,
                rationale=(
                    f"BANKRUPTCY: {self.record.firm_key} was killed "
                    f"({self.record.kill_reason or 'no reason recorded'}). "
                    f"Closing {held.symbol} in full to return the capital."
                ),
                as_of=as_of,
            ))
        return out

    @staticmethod
    def _gene_value(genome, name, default):
        try:
            return D((genome or {}).get(name, default))
        except Exception:  # noqa: BLE001 - a malformed gene is a default gene
            return D(default)

    def _stops(self, market: MarketData, positions: Sequence[Position], as_of) -> list:
        """Close anything that has fallen further than the firm will tolerate.

        Reads `stop_loss_pct` from the genome, so the level is a gene and
        evolution can find the right one per firm — a crypto desk and a rates
        desk should not have the same tolerance, and neither should be a number
        I picked. Zero switches it off, which is what every firm did before
        this existed.

        The whole position goes, not half of it. A stop that closes a fraction
        is not a stop, it is an opinion about size, and the reason this exists
        is that opinions were what kept the losers on the books.

        Unpriceable positions are left alone: a symbol with no mark reads as a
        total loss, and selling on that is the blind-feed massacre with extra
        steps.
        """
        limit = self._gene_value(self.record.genome, "stop_loss_pct", 0)
        if limit <= 0:
            return []

        out = []
        for held in positions:
            if not held.is_open:
                continue
            entry = D(held.avg_price)
            if entry <= 0 or held.symbol in getattr(market, "unpriceable", {}):
                continue
            mark = price(market.mark(held.symbol))
            if mark <= 0 or market.bar(held.symbol) is None:
                continue
            move = (mark - entry) / entry * D(100)
            # Signed for the direction held: a short is losing when price rises.
            against = -move if held.quantity > 0 else move
            if against < limit:
                continue
            out.append(TradeProposal(
                firm_id=self.record.id,
                symbol=held.symbol,
                side=(Side.SELL if held.quantity > 0 else Side.BUY).value,
                quantity=qty(abs(held.quantity)),
                confidence=D(100),
                reference_price=mark,
                rationale=(
                    f"STOP: {held.symbol} is {percent(against)}% against a "
                    f"{limit}% stop (in at {entry}, now {mark}). Closed whole, "
                    "without a debate."
                ),
                as_of=as_of,
            ))
        return out

    # -- where a proposal comes from ---------------------------------------
    def _from_pod(self, market, positions, as_of) -> list:
        """The built-in analysts, debate and trader."""
        out = []
        for symbol in self.universe:
            debate, signals = self.deliberate(symbol, market)
            if not debate.is_decided:
                continue
            reference = price(market.mark(symbol))
            proposal = self.trader.propose(
                self.record, debate, signals, reference, positions, as_of=as_of
            )
            if proposal is not None:
                proposal.reference_price = reference
                out.append(proposal)
        return out

    def _from_bot(self, market, positions, equity, as_of) -> list:
        """Somebody else's file, run as this firm's strategy.

        Everything that can go wrong with running a stranger's code ends here
        as an empty list and a note in the rationale of nothing — the firm has
        a quiet tick and the village is untouched. See src/trading/adapter.py.
        """
        path = adapter.bot_path(self.record.strategy)
        try:
            func = adapter.load(path)
        except adapter.AdapterError as exc:
            self.bot_complaints = [str(exc)]
            return []

        context = adapter.build_context(self.record, market, positions, equity)
        result, error = adapter.call(func, context)
        if error:
            self.bot_complaints = [f"{path.name}: {error}"]
            return []

        out, complaints = adapter.to_proposals(result, self.record, context, as_of)
        self.bot_complaints = [f"{path.name}: {why}" for why in complaints]
        return out

    def _review(self, proposal, reference, positions, equity):
        """The same review every proposal gets, wherever it came from.

        A bot's order is not privileged: it meets the identical risk limits,
        the identical position sizing and the identical audit row. This is the
        whole reason running foreign code is acceptable — the file decides what
        it wants, and nothing downstream cares who asked.
        """
        # A paused firm may still exit. That is the asymmetry, enforced at
        # the one place a paused firm could otherwise open new risk.
        if self.record.status == FirmStatus.PAUSED.value and proposal.side_enum.sign > 0:
            return None

        halted = ""
        if proposal.side_enum.sign > 0:
            halted = self._daily_loss_halt(equity) or self._opens_cap()
        if halted:
            proposal.risk_verdict = RiskVerdict.BLOCK.value
            proposal.risk_reason = halted
            proposal.quantity = ZERO
            proposal.notional = ZERO
            proposal.status = "rejected"
            return proposal

        decision: RiskDecision = self.risk_manager.review(
            self.record,
            proposal.symbol,
            proposal.side_enum,
            proposal.quantity,
            reference,
            positions,
            equity,
        )
        proposal.risk_verdict = decision.verdict
        proposal.risk_reason = decision.reason
        proposal.quantity = decision.quantity
        proposal.notional = money(decision.quantity * reference)
        if decision.blocked:
            proposal.status = "rejected"
        return proposal

    # -- lessons from other trading villages (2026-10-05) -----------------
    #: Bars over which a trade's typical move is measured against its costs.
    EDGE_BARS = 16
    #: The typical move must be at least this many round trips of cost.
    EDGE_COST_MULTIPLE = D("1.5")

    def _lessons(self, proposal, market) -> str:
        """Shrink or refuse a new position. Returns why it is refused, or "".

        Three rules from the trading villages surveyed on 2026-10-05
        (village-health/other-trading-villages-2026-10-05.md):

        * **The move must pay for the trade.** The 500-agent "Galaxy Empire"
          village was right 51% of the time and needed about 55% to cover its
          fees. If the symbol's typical move over the next few bars is smaller
          than one and a half round trips of this firm's costs, there is
          nothing to win, so the trade is refused.
        * **Smaller after losses.** FinMem's agents grow cautious after a bad
          run; ours bet the same size until the kill switch. Each loss in a
          row takes 15% off a new position, down to a quarter.
        * **Smaller when it is swinging.** A real-money fleet of 500 LLM agents
          held about 5x leverage whatever the market did. A symbol moving more
          than usual gets a position cut in proportion, down to a quarter.

        Exits are never touched: the way out is always open.
        """
        try:
            closes = [float(c) for c in (market.closes(proposal.symbol) or []) if c]
        except Exception:  # noqa: BLE001 - no history, judge on losses alone
            closes = []
        cost = getattr(self, "round_trip_cost", None)
        if cost and len(closes) > self.EDGE_BARS + 20:
            n = self.EDGE_BARS
            moves = sorted(abs(closes[i + n] / closes[i] - 1)
                           for i in range(max(0, len(closes) - 200 - n), len(closes) - n))
            typical = moves[len(moves) // 2]
            if typical < float(cost) * float(self.EDGE_COST_MULTIPLE):
                return (f"edge below costs: {proposal.symbol} typically moves "
                        f"{typical * 100:.2f}% over {n} bars, less than "
                        f"{float(self.EDGE_COST_MULTIPLE)}x the {float(cost) * 100:.2f}% "
                        "round trip")

        factor, notes = D("1"), []
        losses = int(getattr(self.record, "consecutive_losses", 0) or 0)
        if losses > 0:
            f = max(D("0.25"), D("1") - D("0.15") * losses)
            factor *= f
            notes.append(f"{losses} loss(es) in a row: size x{f}")
        if len(closes) > 60:
            rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
            recent, usual = rets[-20:], rets[-200:]

            def sd(x):
                m = sum(x) / len(x)
                return (sum((v - m) ** 2 for v in x) / len(x)) ** 0.5
            now, normal = sd(recent), sd(usual)
            if now > 0 and normal > 0 and now > normal:
                f = max(D("0.25"), D(str(round(normal / now, 4))))
                factor *= f
                notes.append(f"swinging {now / normal:.1f}x its usual: size x{f}")
        if factor < 1:
            proposal.quantity = qty(D(proposal.quantity) * factor)
            if D(proposal.reference_price or 0) > 0:
                proposal.notional = money(proposal.quantity * D(proposal.reference_price))
            proposal.rationale = ((proposal.rationale or "") + " | " + "; ".join(notes)).strip(" |")
            if proposal.quantity <= 0:
                return "sized to nothing: " + "; ".join(notes)
        return ""

    def _opens_cap(self) -> str:
        """Why the firm may not open another position today, or "" if it may."""
        cap = int(getattr(self.risk_manager.limits, "max_opens_per_day", 0) or 0)
        if cap <= 0 or self.opens_today is None:
            return ""
        try:
            opened = int(self.opens_today() or 0)
        except Exception:  # noqa: BLE001 - a missing ledger never blocks a trade
            return ""
        if opened < cap:
            return ""
        return (f"trade cap: {opened} positions opened today (limit {cap}); "
                "exits only until tomorrow")

    def _daily_loss_halt(self, equity) -> str:
        """Why the firm may not open risk today, or "" if it may."""
        limit = D(getattr(self.risk_manager.limits, "max_daily_loss_pct", ZERO) or ZERO)
        if limit <= 0 or self.day_open_equity is None:
            return ""
        try:
            opened = self.day_open_equity()
        except Exception:  # noqa: BLE001 - a missing history never blocks a trade
            return ""
        if not opened or D(opened) <= 0:
            return ""
        down = (D(opened) - D(equity)) / D(opened)
        if down < limit:
            return ""
        return (f"daily loss halt: equity {money(D(equity))} is {percent(down * 100)}% below "
                f"today's open of {money(D(opened))} (limit {percent(limit * 100)}%); "
                "exits only until tomorrow")

    # -- self-preservation -------------------------------------------------
    def check_kill(self, metrics: FirmMetrics):
        """Evaluate the firm's own kill conditions. Decides nothing by itself."""
        return self.kill_switch.evaluate(metrics)

    def pause(self, reason: str) -> dict:
        """Stop opening risk. Always allowed, never needs anyone's permission."""
        self.record.status = FirmStatus.PAUSED.value
        return self.kill_switch.trigger(self.key, reason)


__all__ = ["Firm"]
