"""The Trader — turns a won debate into a sized order.

The trader has no opinion of its own. It receives a verdict from the debate
room and converts it into a quantity, which is the only judgement it makes.
Two rules govern that conversion:

* Conviction sizes the trade. A debate won 51/49 produces a token position;
  one won 90/10 produces the full risk budget. Confidence is not a filter
  applied after sizing, it *is* the sizing.
* Selling is never gated on confidence. If the bear case wins on a name the
  firm holds, it sells — the exit does not have to argue as hard as the entry
  did. Same asymmetry as everywhere else in the village.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ...money import D, ZERO, money
from ..config import FirmDefaults
from ..models import (
    FirmRecord,
    Position,
    ProposalStatus,
    Side,
    Signal,
    TradeProposal,
    price,
    qty,
)
from .researchers import DebateResult


class Trader:
    def __init__(self, limits: Optional[FirmDefaults] = None):
        self.limits = limits or FirmDefaults()

    def propose(
        self,
        firm: FirmRecord,
        debate: DebateResult,
        signals: Sequence[Signal],
        reference_price: Decimal,
        positions: Sequence[Position],
        as_of=None,
    ) -> Optional[TradeProposal]:
        """Return a proposal, or None when the right move is to do nothing."""
        if not debate.is_decided:
            return None

        reference_price = price(reference_price)
        if reference_price <= 0:
            return None

        held = next(
            (p.quantity for p in positions if p.symbol == debate.symbol and p.is_open), ZERO
        )
        side = debate.side

        if side is Side.SELL:
            if held <= 0:
                return None  # nothing to exit; the firm does not short
            # A losing argument for holding is enough to leave. How much
            # leaves scales with how badly it lost.
            fraction = D("1") if debate.confidence >= D(60) else D("0.5")
            quantity = qty(held * fraction)
            rationale = (
                f"Bear case won {debate.bear_weight} to {debate.bull_weight}; "
                f"reducing {debate.symbol} by {fraction * 100}% of the holding."
            )
        else:
            if debate.confidence < self.limits.min_confidence:
                return None  # won, but not convincingly enough to open risk
            budget = money(
                firm.allocation * D(firm.risk_limit) * (debate.confidence / D(100))
            )
            if budget <= 0:
                return None
            quantity = qty(budget / reference_price)
            if quantity <= 0:
                return None
            rationale = (
                f"Bull case won {debate.bull_weight} to {debate.bear_weight} "
                f"(confidence {debate.confidence}); risking {budget} of "
                f"{money(firm.allocation)} allocation."
            )

        return TradeProposal(
            firm_id=firm.id,
            symbol=debate.symbol,
            side=side.value,
            quantity=quantity,
            reference_price=reference_price,
            notional=money(quantity * reference_price),
            confidence=debate.confidence,
            signals=list(signals),
            bull_case=debate.bull_case,
            bear_case=debate.bear_case,
            debate_winner=debate.winner,
            rationale=rationale,
            status=ProposalStatus.PROPOSED.value,
            as_of=as_of,
        )


__all__ = ["Trader"]
