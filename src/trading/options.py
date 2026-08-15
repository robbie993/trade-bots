"""Option contracts, and why the village's limits do not fit them.

**The instrument is the easy half.** A contract is a strike, an expiry, a right
and a multiplier, and OCC already standardised how to write one down. That part
is bookkeeping and it is below.

**The gates are the hard half, and they are the reason this took so long to
start.** Every risk limit in this village was written for a linear instrument,
and each one means something different — or nothing at all — once an option is
in the book:

``max_drawdown_pct``
    A long call that expires worthless has lost 100% of its premium *on
    schedule*. That is the instrument working as designed, not a firm that blew
    up, and a limit that cannot tell those apart will kill every option firm it
    ever supervises. Options are therefore measured on **premium at risk**
    rather than on position value, and a contract inside its final days is
    exempted from drawdown entirely — see `is_expiring`.

``max_position_pct``
    Notional and premium differ by the multiplier and then by leverage. One
    contract on a $500 underlying is $50,000 of exposure for maybe $300 of
    premium. Sizing on premium understates the risk by two orders of magnitude;
    sizing on notional makes every option position look like a violation. Both
    numbers are computed here and named differently so a caller has to say
    which one it means.

``max_single_loss_pct``
    Survives intact for long options, because the premium is the maximum loss
    and that is knowable in advance. It does **not** survive for short options,
    where the loss is unbounded — so `max_loss` returns `None` there, and every
    caller has to decide what to do about not knowing rather than being handed
    a comforting number.

**What this module will not do.** Price an option. There is no Black-Scholes
here and there will not be one, because a model price is not a mark and this
village has a standing rule about the difference: a number the feed did not
give us is not evidence. An option with no quote is `unpriceable`, exactly like
a stock with no bar, and the kill switch already knows what to do with that.

Nothing here trades. It is the vocabulary the rest of the system needs before
an option can be held at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money

#: Shares per contract. A hundred everywhere that matters; carried on the
#: contract anyway, because adjusted contracts exist and a hard-coded 100 is a
#: silent factor-of-anything error on the day one turns up.
STANDARD_MULTIPLIER = 100

#: Days to expiry inside which a contract is no longer judged on drawdown.
#: Premium decaying to nothing in the last week is the instrument doing what it
#: says on the tin.
EXPIRY_GRACE_DAYS = 7

CALL = "C"
PUT = "P"

#: The OCC symbol: root, YYMMDD, C or P, then the strike in thousandths.
#:     SPY   260918C00450000  ->  SPY 18 Sep 2026 450 call
OCC = re.compile(r"^(?P<root>[A-Z][A-Z0-9.]{0,5})\s*"
                 r"(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})"
                 r"(?P<right>[CP])(?P<strike>\d{8})$")


class OptionSymbolError(ValueError):
    """Not an option symbol. Raised rather than guessed at."""


@dataclass(frozen=True)
class Contract:
    """One option, as written down. Holds no opinion about what it is worth."""

    underlying: str
    expiry: date
    strike: Decimal
    right: str                       # CALL or PUT
    multiplier: int = STANDARD_MULTIPLIER

    @property
    def is_call(self) -> bool:
        return self.right == CALL

    @property
    def symbol(self) -> str:
        """Back to OCC, so a round trip through this module changes nothing."""
        thousandths = int((self.strike * D(1000)).to_integral_value())
        return (f"{self.underlying}{self.expiry:%y%m%d}"
                f"{self.right}{thousandths:08d}")

    def days_to_expiry(self, as_of) -> Optional[int]:
        """Calendar days left. `None` when the caller cannot say what day it is.

        Not zero, and not a guess. Every limit below turns on this number, and
        "I do not know" has to stay distinguishable from "it expires today".
        """
        today = _as_date(as_of)
        return None if today is None else (self.expiry - today).days

    def is_expired(self, as_of) -> bool:
        left = self.days_to_expiry(as_of)
        return left is not None and left < 0

    def is_expiring(self, as_of, grace: int = EXPIRY_GRACE_DAYS) -> bool:
        """Inside the window where decay is the instrument, not a loss."""
        left = self.days_to_expiry(as_of)
        return left is not None and left <= grace

    def intrinsic(self, spot) -> Decimal:
        """What it would be worth exercised right now, per share. Never negative.

        This is arithmetic on the strike, not a valuation: it says nothing
        about time value and must not be used as a mark.
        """
        price = D(spot)
        if price <= 0:
            return ZERO
        gap = (price - self.strike) if self.is_call else (self.strike - price)
        return money(max(ZERO, gap))

    def notional(self, spot) -> Decimal:
        """The underlying exposure one contract represents."""
        return money(D(spot) * D(self.multiplier))

    def premium(self, quote) -> Decimal:
        """What one contract costs at this quote."""
        return money(D(quote) * D(self.multiplier))

    def __str__(self) -> str:
        kind = "call" if self.is_call else "put"
        return f"{self.underlying} {self.expiry:%d %b %Y} {self.strike} {kind}"


def parse(symbol: str) -> Contract:
    """Read an OCC symbol. Raises rather than returning something plausible."""
    text = str(symbol or "").strip().upper().replace(" ", "")
    match = OCC.match(text)
    if match is None:
        raise OptionSymbolError(
            f"{symbol!r} is not an OCC option symbol "
            "(expected e.g. SPY260918C00450000)"
        )
    try:
        expiry = date(2000 + int(match["y"]), int(match["m"]), int(match["d"]))
    except ValueError as exc:
        raise OptionSymbolError(f"{symbol!r} has no such expiry date") from exc
    return Contract(
        underlying=match["root"],
        expiry=expiry,
        strike=money(D(match["strike"]) / D(1000)),
        right=match["right"],
    )


def is_option(symbol: str) -> bool:
    """Cheap test. Everything that is not an option is a plain symbol."""
    return OCC.match(str(symbol or "").strip().upper().replace(" ", "")) is not None


def contract_size(symbol: str) -> int:
    """Shares one unit of this symbol controls. 100 for options, 1 otherwise.

    **This is the number that makes an option position mean anything**, and it
    is read from the symbol rather than stored on the row on purpose. A stored
    multiplier is a column that can be wrong — NULL on rows written before the
    migration, 1 on an option written by a code path that forgot, 100 on a
    stock by a bad backfill — and every one of those is a silent
    factor-of-a-hundred error in cash, equity, drawdown and the position cap.
    The symbol already carries the fact. Deriving it means a row cannot
    disagree with itself, and every stock position ever written is correct
    without touching the database.
    """
    return STANDARD_MULTIPLIER if is_option(symbol) else 1


def try_parse(symbol: str) -> Optional[Contract]:
    try:
        return parse(symbol)
    except OptionSymbolError:
        return None


# =========================================================================
# the part the gates need
# =========================================================================
def max_loss(contract: Contract, quantity, quote) -> Optional[Decimal]:
    """The most this position can lose. `None` when that is unbounded.

    Long options: the premium, and no more. That is the whole appeal.

    Short options: unbounded for a call, and merely enormous for a put. This
    returns `None` for both rather than the put's arithmetic maximum, because
    a caller that gets a number will use it as a limit, and the number for a
    naked short is not a limit anybody should be sizing against.
    """
    held = D(quantity)
    if held == 0:
        return ZERO
    if held < 0:
        return None
    return money(abs(held) * contract.premium(quote))


def premium_at_risk(contract: Contract, quantity, quote) -> Decimal:
    """What is actually on the table, for a drawdown that means something.

    Position *value* falls to zero on a worthless expiry and reads as a total
    loss. Premium at risk says the same thing in the units the loss was always
    denominated in, which is what makes a limit comparable across a stock book
    and an option book.
    """
    return money(abs(D(quantity)) * contract.premium(quote))


def counts_toward_drawdown(contract: Contract, as_of) -> bool:
    """Whether this contract's decay should be read as a firm losing money.

    False inside the expiry window: premium going to nothing there is the
    instrument expiring, which is the thing options do. A kill switch that
    cannot tell that apart from a blown-up book will kill every option firm it
    ever supervises, and it will do it on schedule.
    """
    return not contract.is_expiring(as_of)


def exposure(contract: Contract, quantity, spot) -> Decimal:
    """Signed underlying exposure — the number a position limit should read.

    A long call and a short put are both long the underlying. Sizing on premium
    would call one contract on a $500 name a $300 position when it is $50,000
    of exposure.
    """
    held = D(quantity)
    if held == 0 or D(spot) <= 0:
        return ZERO
    direction = D(1) if contract.is_call else D(-1)
    return money(held * direction * contract.notional(spot))


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


__all__ = [
    "CALL", "Contract", "EXPIRY_GRACE_DAYS", "OCC", "OptionSymbolError", "PUT",
    "STANDARD_MULTIPLIER", "contract_size", "counts_toward_drawdown", "exposure",
    "is_option", "max_loss", "parse", "premium_at_risk", "try_parse",
]
