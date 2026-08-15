"""What happens to an option on the day it stops existing.

Every other position in this village ends because somebody decided to end it.
An option ends on a date fixed when it was opened, whether or not anyone is
watching, and if nothing handles that the book keeps a contract that expired
last March at whatever it was last marked at. That is not a stale price; it is
a position that does not exist being carried as though it does.

**This is the one moment an option's value is arithmetic rather than a model.**
The rest of the time a contract is worth what somebody will pay for it, which
is why `options.py` refuses to price one. At expiry the time value is gone by
definition and what remains is `max(0, spot - strike)` for a call and the
mirror for a put — the strike, the spot, and subtraction. No Black-Scholes, no
implied volatility, nothing this repository would have to apologise for.

**What this models, and what it does not.** Positions are closed for cash at
intrinsic value. Real equity options are *assigned*: an in-the-money long call
turns into a hundred shares and takes `strike × 100` of cash to do it, which is
capital a firm may not have. Cash settlement gets the P&L exactly right and the
capital requirement wrong, and it is wrong in the flattering direction — it
never bounces a firm that could not actually have afforded to exercise. That
limitation is named here rather than buried, and `SETTLES_FOR_CASH` exists so
a caller can find out without reading the source.

**The refusals.** A contract whose underlying cannot be priced on the day is
not settled at zero, and not settled at a guess. It is left alone and the
refusal is recorded, which puts the firm into the `CANNOT_VALUE` state the
village already knows what to do with. Settling an unpriceable contract at zero
would silently book a total loss on a position that might have been worth a
fortune, and it would look exactly like a strategy that failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, fmt_money, money
from .models import Fill, Side
from .options import Contract, try_parse

#: Positions are closed for cash at intrinsic value rather than assigned into
#: the underlying. See the module docstring for what that gets wrong.
SETTLES_FOR_CASH = True


@dataclass
class Settlement:
    """One expired contract, and what became of it."""

    firm_key: str
    symbol: str
    quantity: Decimal = ZERO
    spot: Optional[Decimal] = None
    intrinsic: Decimal = ZERO
    proceeds: Decimal = ZERO
    realized: Decimal = ZERO
    refused: str = ""

    @property
    def expired_worthless(self) -> bool:
        return not self.refused and self.intrinsic == 0

    def __str__(self) -> str:
        if self.refused:
            return f"{self.firm_key} {self.symbol}: not settled — {self.refused}"
        if self.expired_worthless:
            return (f"{self.firm_key} {self.symbol}: expired worthless "
                    f"({self.quantity} contract(s)), {fmt_money(self.realized)}")
        return (f"{self.firm_key} {self.symbol}: settled at "
                f"{fmt_money(self.intrinsic)} intrinsic on a "
                f"{fmt_money(self.spot)} underlying, "
                f"{fmt_money(self.proceeds)} in, {fmt_money(self.realized)} realised")


@dataclass
class ExpiryReport:
    settled: list = field(default_factory=list)
    refused: list = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.settled or self.refused)

    def summary(self) -> str:
        if not self.any:
            return "no contracts expired"
        lines = [f"{len(self.settled)} contract(s) settled, "
                 f"{len(self.refused)} left unsettled"]
        lines += [f"  {s}" for s in (*self.settled, *self.refused)]
        return "\n".join(lines)


def _spot_for(market, contract: Contract) -> Optional[Decimal]:
    """The underlying's price, or None. Never a fallback, never a guess."""
    try:
        market.register([contract.underlying])
    except Exception:  # noqa: BLE001 - a feed that cannot register can still mark
        pass
    try:
        mark = market.mark(contract.underlying)
    except Exception:  # noqa: BLE001 - an unpriceable underlying is a refusal
        return None
    if mark is None:
        return None
    spot = D(mark)
    return spot if spot > 0 else None


def settle_firm(store, firm, market, as_of) -> ExpiryReport:
    """Close every expired option this firm holds. Writes to the ledger.

    The closing trade goes through ``store.settle`` like any other fill, so
    the reconciler's identities hold across an expiry without expiry needing
    to know what they are. A contract that expires worthless closes at a price
    of zero, which books the whole premium as a realised loss — correct, and
    the reason `options.counts_toward_drawdown` exists to stop the kill switch
    reading it as a blown-up book.
    """
    report = ExpiryReport()

    for position in store.positions(firm.id):
        if not position.is_open:
            continue
        contract = try_parse(position.symbol)
        if contract is None or not contract.is_expired(as_of):
            continue

        out = Settlement(firm_key=firm.firm_key, symbol=position.symbol,
                         quantity=position.quantity)

        spot = _spot_for(market, contract)
        if spot is None:
            # Not zero. A contract we could not price might have been worth a
            # fortune, and booking it as a total loss would look exactly like
            # a strategy that failed.
            out.refused = (f"{contract.underlying} could not be priced on "
                           f"{contract.expiry:%d %b %Y}")
            report.refused.append(out)
            store.record_event(
                "expiry_unpriced", str(out), firm_id=firm.id,
                payload={"symbol": position.symbol, "expiry": str(contract.expiry)},
            )
            continue

        out.spot = money(spot)
        out.intrinsic = contract.intrinsic(spot)

        # Closing trade: the opposite side, for the whole position. A long is
        # sold back, a short is bought back — including a short assigned
        # against, where the intrinsic value is what it costs to be rid of it.
        closing_side = Side.SELL if position.quantity > 0 else Side.BUY
        fill = Fill(
            firm_id=firm.id,
            symbol=position.symbol,
            side=closing_side.value,
            quantity=abs(position.quantity),
            price=out.intrinsic,
            fee=ZERO,      # expiry and assignment cross no spread
            slippage=ZERO,
            venue="expiry",
            as_of=as_of,
        )
        store.settle(firm, fill)
        out.proceeds = money(fill.cash_delta)
        out.realized = money(fill.realized_pnl)

        report.settled.append(out)
        store.record_event(
            "expiry_settled", str(out), firm_id=firm.id,
            payload={"symbol": position.symbol, "spot": str(out.spot),
                     "intrinsic": str(out.intrinsic),
                     "realized": str(out.realized),
                     "cash_settled": SETTLES_FOR_CASH},
        )

    return report


def settle_all(store, firms, market, as_of) -> ExpiryReport:
    """Every firm's expiries, in one report."""
    whole = ExpiryReport()
    for firm in firms:
        one = settle_firm(store, firm, market, as_of)
        whole.settled += one.settled
        whole.refused += one.refused
    return whole


__all__ = ["ExpiryReport", "SETTLES_FOR_CASH", "Settlement", "settle_all",
           "settle_firm"]
