"""Decimal helpers.

Every monetary value and every derived percentage in this system is a
``decimal.Decimal``. Binary floats are never used for cash: a store dies from
cash, not from P&L (spec section 12), and a ledger that drifts by fractions of
a cent is not auditable.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union

Number = Union[int, float, str, Decimal, None]

MONEY = Decimal("0.01")  # DECIMAL(x,2)
PERCENT = Decimal("0.01")  # DECIMAL(5,2)
ZERO = Decimal("0")


def D(value: Number, default: Decimal = ZERO) -> Decimal:
    """Coerce anything to Decimal without going through binary float."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; never intended as money
        raise TypeError("refusing to coerce bool to Decimal")
    return Decimal(str(value))


def money(value: Number) -> Decimal:
    """Round to cents, half-up (what an accountant expects, not banker's)."""
    return D(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def percent(value: Number) -> Decimal:
    """Round a percentage to two places, matching DECIMAL(5,2) storage.

    Kill decisions are made on the rounded value on purpose: the number stored
    in the ledger must be the number that justified the decision.
    """
    return D(value).quantize(PERCENT, rounding=ROUND_HALF_UP)


def safe_div(numerator: Number, denominator: Number) -> Optional[Decimal]:
    """Divide, or return None when the denominator is zero/absent.

    A missing ratio is *not* zero. Returning None forces callers to decide what
    "no data yet" means instead of silently reading 0% CTR as a kill signal.
    """
    n, d = D(numerator), D(denominator)
    if d == 0:
        return None
    return n / d


def ratio_pct(numerator: Number, denominator: Number) -> Decimal:
    """Percentage, defaulting to 0 when there is no denominator."""
    result = safe_div(numerator, denominator)
    return percent(ZERO if result is None else result * 100)


def fmt_money(value: Number) -> str:
    v = money(value)
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def fmt_pct(value: Number) -> str:
    return f"{percent(value):.2f}%"
