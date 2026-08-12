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

from ...money import ZERO, money
from ..config import FirmDefaults, FirmKillConfig, TradingConfig
from ..data.market_data import MarketData
from .. import adapter
from ..models import FirmRecord, FirmStatus, Position, Signal, TradeProposal, price
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
        if self.record.status == FirmStatus.KILLED.value:
            return []

        equity = self.equity(market, positions)
        as_of = market.as_of()
        proposals: list[TradeProposal] = []

        raw = (
            self._from_bot(market, positions, equity, as_of)
            if adapter.is_bot(self.record.strategy or "")
            else self._from_pod(market, positions, as_of)
        )

        for proposal in raw:
            reference = proposal.reference_price or price(market.mark(proposal.symbol))
            reviewed = self._review(proposal, reference, positions, equity)
            if reviewed is not None:
                proposals.append(reviewed)

        return proposals

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

    # -- self-preservation -------------------------------------------------
    def check_kill(self, metrics: FirmMetrics):
        """Evaluate the firm's own kill conditions. Decides nothing by itself."""
        return self.kill_switch.evaluate(metrics)

    def pause(self, reason: str) -> dict:
        """Stop opening risk. Always allowed, never needs anyone's permission."""
        self.record.status = FirmStatus.PAUSED.value
        return self.kill_switch.trigger(self.key, reason)


__all__ = ["Firm"]
