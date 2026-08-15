"""Option contracts, and the limits that do not fit them.

The instrument is bookkeeping. The interesting tests are the ones about the
gates: every risk limit in this village was written for a linear instrument,
and each one means something different — or nothing — once an option is held.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.trading import options
from src.trading.options import CALL, PUT, Contract, OptionSymbolError, parse


# =========================================================================
# reading and writing a contract
# =========================================================================
def test_an_occ_symbol_round_trips():
    """A round trip through this module must change nothing, or the symbol we
    store stops matching the symbol the broker knows."""
    c = parse("SPY260918C00450000")
    assert c.underlying == "SPY"
    assert c.expiry == date(2026, 9, 18)
    assert c.strike == Decimal("450.00")
    assert c.is_call
    assert c.symbol == "SPY260918C00450000"


def test_puts_and_fractional_strikes_survive():
    c = parse("QQQ261218P00387500")
    assert c.right == PUT and not c.is_call
    assert c.strike == Decimal("387.50")
    assert c.symbol == "QQQ261218P00387500"


def test_nonsense_raises_rather_than_guessing():
    """A symbol we cannot read must not become a contract we invented."""
    for bad in ("SPY", "", None, "SPY260918X00450000", "SPY261399C00450000"):
        with pytest.raises(OptionSymbolError):
            parse(bad)


def test_plain_symbols_are_not_options():
    assert options.is_option("SPY260918C00450000")
    for plain in ("SPY", "BTC-USD", "BRK.B", ""):
        assert not options.is_option(plain)
    assert options.try_parse("SPY") is None


# =========================================================================
# the drawdown limit, which is the one that would kill every option firm
# =========================================================================
def test_a_contract_near_expiry_is_not_judged_on_drawdown():
    """A long call decaying to nothing in its last week is the instrument
    working as designed. A limit that cannot tell that from a blown-up book
    kills every option firm it supervises, on schedule."""
    c = parse("SPY260918C00450000")
    assert options.counts_toward_drawdown(c, date(2026, 6, 1)) is True
    assert options.counts_toward_drawdown(c, date(2026, 9, 15)) is False
    assert options.counts_toward_drawdown(c, date(2026, 9, 18)) is False


def test_an_unknown_date_does_not_grant_the_exemption():
    """"I do not know what day it is" must not read as "it is nearly expiry"."""
    c = parse("SPY260918C00450000")
    assert c.days_to_expiry(None) is None
    assert c.is_expiring(None) is False
    assert options.counts_toward_drawdown(c, None) is True


def test_expiry_is_a_date_not_a_feeling():
    c = parse("SPY260918C00450000")
    assert c.days_to_expiry(date(2026, 9, 11)) == 7
    assert c.is_expired(date(2026, 9, 19)) is True
    assert c.is_expired(date(2026, 9, 18)) is False
    assert c.days_to_expiry(datetime(2026, 9, 11, 15, 30, tzinfo=timezone.utc)) == 7


# =========================================================================
# the position limit, which reads the wrong number by two orders of magnitude
# =========================================================================
def test_exposure_is_the_underlying_not_the_premium():
    """One contract on a $500 name is $50,000 of exposure for a few hundred
    dollars of premium. Sizing on premium understates it a hundredfold."""
    c = parse("SPY260918C00450000")
    assert c.notional(Decimal("500")) == Decimal("50000.00")
    assert c.premium(Decimal("3")) == Decimal("300.00")
    assert options.exposure(c, 1, Decimal("500")) == Decimal("50000.00")


def test_a_short_put_is_long_the_underlying():
    """Both a long call and a short put want the price to go up. A limit that
    signs them the same way is not measuring direction."""
    call = parse("SPY260918C00450000")
    put = parse("SPY260918P00450000")
    assert options.exposure(call, 1, Decimal("500")) > 0
    assert options.exposure(put, -1, Decimal("500")) > 0
    assert options.exposure(put, 1, Decimal("500")) < 0


def test_a_flat_or_unpriceable_position_has_no_exposure():
    c = parse("SPY260918C00450000")
    assert options.exposure(c, 0, Decimal("500")) == 0
    assert options.exposure(c, 1, Decimal("0")) == 0


# =========================================================================
# the single-loss limit, which only half survives
# =========================================================================
def test_a_long_option_cannot_lose_more_than_it_cost():
    c = parse("SPY260918C00450000")
    assert options.max_loss(c, 2, Decimal("3")) == Decimal("600.00")
    assert options.max_loss(c, 0, Decimal("3")) == 0


def test_a_short_option_has_no_maximum_loss_and_says_so():
    """`None`, not a large number. A caller handed a figure will size against
    it, and the figure for a naked short is not a limit anyone should use."""
    call = parse("SPY260918C00450000")
    put = parse("SPY260918P00450000")
    assert options.max_loss(call, -1, Decimal("3")) is None
    assert options.max_loss(put, -1, Decimal("3")) is None


def test_premium_at_risk_is_unsigned_and_in_the_right_units():
    c = parse("SPY260918C00450000")
    assert options.premium_at_risk(c, -2, Decimal("3")) == Decimal("600.00")
    assert options.premium_at_risk(c, 2, Decimal("3")) == Decimal("600.00")


# =========================================================================
# what this module refuses to do
# =========================================================================
def test_intrinsic_value_is_arithmetic_not_a_mark():
    """It says nothing about time value and must never be used as a price."""
    call = parse("SPY260918C00450000")
    put = parse("SPY260918P00450000")
    assert call.intrinsic(Decimal("500")) == Decimal("50.00")
    assert call.intrinsic(Decimal("400")) == 0, "never negative"
    assert put.intrinsic(Decimal("400")) == Decimal("50.00")
    assert put.intrinsic(Decimal("500")) == 0


def test_there_is_no_pricing_model_here():
    """A model price is not a mark. An option with no quote is unpriceable,
    exactly like a stock with no bar — and the kill switch already knows what
    to do with that."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(options.__file__).read_text())
    # The docstring says there is no pricing model. This checks the code
    # rather than the prose, so the promise cannot rot into a comment.
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for banned in ("price", "black_scholes", "implied_vol", "delta", "gamma",
                   "theta", "vega", "fair_value"):
        assert banned not in defined, f"{banned}() has no business in this file"


def test_the_multiplier_lives_on_the_contract():
    """Adjusted contracts exist, and a hard-coded 100 is a silent
    factor-of-anything error on the day one turns up."""
    odd = Contract(underlying="XYZ", expiry=date(2026, 9, 18),
                   strike=Decimal("50"), right=CALL, multiplier=10)
    assert odd.notional(Decimal("100")) == Decimal("1000.00")
    assert parse("SPY260918C00450000").multiplier == 100
