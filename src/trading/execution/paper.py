"""The paper venue — the only venue that trades without asking.

Fills cross the spread and pay a fee. That matters more than it sounds: a
simulator that fills at the mid for free will report an edge that does not
exist, and every downstream decision — the score, the allocation, which
genome survives — is then made on a number that was never real.

The arithmetic here is the arithmetic the backtester uses, on purpose. A
strategy that looks good in a backtest and bad on paper should be failing on
its predictions, not on two different fill models.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ...money import D, money
from ..config import DataConfig
from ..models import Fill, Side, TradeProposal, price, qty

BPS = D("10000")


@dataclass(frozen=True)
class Quote:
    fill_price: Decimal
    fee: Decimal
    slippage: Decimal
    gross: Decimal


class PaperVenue:
    """Executes against the reference price, with slippage and fees applied."""

    name = "paper"
    is_live = False

    def __init__(self, config: Optional[DataConfig] = None):
        self.config = config or DataConfig()

    def quote(self, side: Side, reference_price: Decimal, quantity: Decimal) -> Quote:
        reference_price = D(reference_price)
        quantity = D(quantity)
        drift = reference_price * D(self.config.slippage_bps) / BPS
        # Slippage always works against the trader: buys fill higher, sells
        # lower. A symmetric-noise model would let it cancel out over many
        # trades, which is precisely the flattering assumption to avoid.
        fill_price = price(reference_price + drift if side is Side.BUY else reference_price - drift)
        gross = money(fill_price * quantity)
        fee = money(gross * D(self.config.fee_bps) / BPS)
        return Quote(
            fill_price=fill_price,
            fee=fee,
            slippage=money(drift * quantity),
            gross=gross,
        )

    def execute(self, proposal: TradeProposal, reference_price: Optional[Decimal] = None) -> Fill:
        reference = D(reference_price if reference_price is not None else proposal.reference_price)
        quote = self.quote(proposal.side_enum, reference, proposal.quantity)
        return Fill(
            firm_id=proposal.firm_id,
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=qty(proposal.quantity),
            price=quote.fill_price,
            fee=quote.fee,
            slippage=quote.slippage,
            venue=self.name,
            proposal_id=proposal.id,
            as_of=proposal.as_of,
        )


__all__ = ["PaperVenue", "Quote"]
