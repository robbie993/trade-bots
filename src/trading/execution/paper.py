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
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ...money import D, money
from .. import session as market_session
from ..config import DataConfig
from ..models import Fill, Side, TradeProposal, price, qty
from ..options import contract_size

BPS = D("10000")


class MarketClosed(RuntimeError):
    """No venue was open, so there was nothing to fill against.

    This is not the village being forbidden a trade it wanted. It is the
    market being shut: US equities have no session between 20:00 and 04:00
    ET, none at weekends and none on ten holidays a year, and an order sent
    into that gap reaches nobody. Crypto never raises this.

    Measured before it existed: 499 of 2,039 fills (24.5%) were booked into a
    closed market — EFA, XOM, JNJ, PG, NVDA — each at a price nobody was
    quoting, each carrying its invented P&L into the ledger and from there
    into the scores that decide which genome survives.
    """


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

    @property
    def session_aware(self) -> bool:
        """Whether a timestamp here names a moment or merely a day.

        A daily bar is stamped midnight UTC — `2026-04-04 00:00:00+00:00` — and
        that is a *label for a session*, not the instant of a trade. Read as an
        instant it is 20:00 ET the evening before, which is shut, so applying
        the clock to daily bars marks every single one closed and refuses the
        entire backtest. It did: 29 tests went red in one run.

        So the session rules apply only where timestamps are moments. On daily
        bars the trade is taken at the close, which is regular hours.
        """
        try:
            return bool(self.config.resolution.is_intraday)
        except Exception:               # a config without a parseable bar
            return False

    def slippage_bps_for(self, symbol: str = "", as_of=None) -> Decimal:
        """What crossing really costs here, in basis points of one side.

        The configured constant is the fallback, not the answer. Measured from
        SIP quotes at the village's own fill timestamps, the real half-spread
        runs 0.82 bps in regular hours, 1.55 pre-market and 5.14 after — a 6x
        range that a single number erases. Erasing it is not neutral: it makes
        a thin overnight market cost exactly what a deep midday one costs, so
        no firm in the village ever had a reason to prefer the liquid hour.
        """
        if as_of is None:
            return D(self.config.slippage_bps)
        if not self.session_aware:
            return market_session.HALF_SPREAD_BPS[market_session.REGULAR]
        return market_session.cost_bps(as_of, symbol)

    def quote(self, side: Side, reference_price: Decimal, quantity: Decimal,
              multiplier: int = 1, symbol: str = "", as_of=None) -> Quote:
        """Price one fill.

        ``multiplier`` is the contract size — 100 for an option, 1 for a
        stock. It scales ``gross``, and therefore the fee, because a fee
        quoted in basis points of a $3.20 quote rather than of the $320
        premium actually paid is a hundredth of the real cost. The fill
        *price* is per share and stays per share; only the money is scaled.

        ``symbol`` and ``as_of`` together say which session this is, and so
        what the spread actually was. Without them the flat constant stands,
        which keeps every existing caller and the backtester unchanged.
        """
        reference_price = D(reference_price)
        quantity = D(quantity)
        drift = reference_price * self.slippage_bps_for(symbol, as_of) / BPS
        # Slippage always works against the trader: buys fill higher, sells
        # lower. A symmetric-noise model would let it cancel out over many
        # trades, which is precisely the flattering assumption to avoid.
        fill_price = price(reference_price + drift if side is Side.BUY else reference_price - drift)
        gross = money(fill_price * quantity * D(multiplier))
        fee = money(gross * D(self.config.fee_bps) / BPS)
        return Quote(
            fill_price=fill_price,
            fee=fee,
            slippage=money(drift * quantity * D(multiplier)),
            gross=gross,
        )

    def execute(self, proposal: TradeProposal, reference_price: Optional[Decimal] = None) -> Fill:
        as_of = _when(proposal.as_of)
        if (as_of is not None and self.session_aware
                and not market_session.is_open(as_of, proposal.symbol)):
            raise MarketClosed(
                f"{proposal.symbol} at {proposal.as_of}: the market was shut — "
                f"{market_session.window(as_of, proposal.symbol).describe()}"
            )
        reference = D(reference_price if reference_price is not None else proposal.reference_price)
        quote = self.quote(proposal.side_enum, reference, proposal.quantity,
                           contract_size(proposal.symbol),
                           symbol=proposal.symbol, as_of=as_of)
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


def _when(as_of) -> Optional[datetime]:
    """Read a proposal's timestamp without ever guessing at one.

    A proposal with an unreadable stamp must not silently become "now" — that
    would price a weekend trade at Tuesday-lunchtime spreads and wave it
    through. When it cannot be read, `None` means "no session opinion", and
    the flat constant applies exactly as before.
    """
    if isinstance(as_of, datetime):
        return as_of
    if not as_of:
        return None
    text = str(as_of).strip().replace("Z", "+00:00").replace(" ", "T")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


__all__ = ["MarketClosed", "PaperVenue", "Quote"]
