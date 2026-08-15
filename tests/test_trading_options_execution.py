"""Holding an option, and the day it stops existing.

Two things are being defended here.

**The multiplier reaches the money.** An option is quoted per share and traded
per contract, so every place that turns quantity into dollars is a
factor-of-a-hundred error waiting to happen — cash, equity, realised P&L, the
position cap, the fee. A stock is a contract of size one, which is why none of
this changed a single existing test.

**An expiry is not a trade.** It happens on a date fixed when the position was
opened, to a firm that may not be watching, and it is the one moment an
option's value is arithmetic rather than a model.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.money import D
from src.trading import expiry, options
from src.trading.execution.paper import PaperVenue
from src.trading.firms.risk_manager import RiskManager
from src.trading.models import Fill, Position, RiskVerdict, Side

# SPY 18 Sep 2026 450 call, and the matching put.
CALL = "SPY260918C00450000"
PUT = "SPY260918P00450000"


class _Market:
    """Prices the underlying and nothing else — which is all expiry needs."""

    def __init__(self, spot="470", missing=False):
        self.spot = None if missing else Decimal(spot)
        self.registered: list = []

    def register(self, symbols):
        self.registered += list(symbols)

    def mark(self, symbol):
        if symbol == "SPY":
            return self.spot
        return None

    def as_of(self):
        return datetime(2026, 9, 21, tzinfo=timezone.utc)   # after expiry


# =========================================================================
# the multiplier, in every place money is computed
# =========================================================================
def test_a_stock_is_a_contract_of_size_one():
    assert options.contract_size("SPY") == 1
    assert options.contract_size("BTC/USD") == 1
    assert options.contract_size(CALL) == 100


def test_a_contract_costs_a_hundred_times_its_quote():
    fill = Fill(firm_id=1, symbol=CALL, side="buy", quantity=D("2"), price=D("3.20"))
    assert fill.gross == Decimal("640.00")          # not $6.40

    stock = Fill(firm_id=1, symbol="SPY", side="buy", quantity=D("2"), price=D("3.20"))
    assert stock.gross == Decimal("6.40")


def test_an_option_position_is_marked_per_contract():
    pos = Position(firm_id=1, symbol=CALL, quantity=D("3"), avg_price=D("2.00"))
    assert pos.multiplier == 100
    assert pos.market_value(D("5.00")) == Decimal("1500.00")
    assert pos.unrealized_pnl(D("5.00")) == Decimal("900.00")   # $3 x 3 x 100


def test_realised_pnl_on_a_contract_carries_the_multiplier():
    pos = Position(firm_id=1, symbol=CALL, quantity=D("2"), avg_price=D("2.00"))
    realized = pos.apply(Fill(firm_id=1, symbol=CALL, side="sell",
                              quantity=D("2"), price=D("3.50")))
    assert realized == Decimal("300.00")            # $1.50 x 2 x 100
    assert not pos.is_open


def test_the_fee_is_charged_on_the_premium_not_on_the_quote():
    """Basis points of a $3.20 quote is a hundredth of the real commission."""
    venue = PaperVenue()
    quote = venue.quote(Side.BUY, D("3.20"), D("2"), multiplier=100)
    plain = venue.quote(Side.BUY, D("3.20"), D("2"))

    # Two contracts at a ~$3.20 fill is ~$640 of premium, not $6.40.
    assert quote.gross > Decimal("600") and quote.gross < Decimal("700")
    assert quote.gross == quote.fill_price * 2 * 100
    assert quote.fee > plain.fee, "the commission is charged on real dollars"
    assert quote.fill_price == plain.fill_price, "the price stays per share"


def test_cash_moves_by_the_contract_value(store, firm_record):
    before = store.require_firm_by_id(firm_record.id).cash
    fill = Fill(firm_id=firm_record.id, symbol=CALL, side="buy",
                quantity=D("2"), price=D("3.20"))
    store.settle(store.require_firm_by_id(firm_record.id), fill)

    assert fill.cash_delta == Decimal("-640.00")
    after = store.require_firm_by_id(firm_record.id).cash
    assert before - after == Decimal("640.00")


# =========================================================================
# the risk manager, which was written for linear instruments
# =========================================================================
def _firm(firm_record):
    firm_record.allocation = D("100000")
    firm_record.cash = D("100000")
    firm_record.risk_limit = D("0.50")
    return firm_record


def test_an_option_is_sized_on_the_dollars_that_leave_the_account(firm_record):
    """Sizing a $3.20 quote as $3.20 of capital buys a hundred times too much."""
    rm = RiskManager()
    decision = rm.review(_firm(firm_record), CALL, Side.BUY, D("1000"),
                         D("3.20"), [], D("100000"))

    assert decision.verdict != RiskVerdict.ALLOW.value, "1000 contracts is $320,000"
    # Whatever it allows must fit inside the firm's capital at $320 a contract.
    assert decision.quantity * Decimal("320") <= Decimal("100000")


def test_writing_an_option_is_refused_rather_than_sized(firm_record):
    """`max_loss` returns None for a short. A cap cannot bound what it cannot
    measure, so this blocks instead of accepting a comforting number."""
    rm = RiskManager()
    decision = rm.review(_firm(firm_record), CALL, Side.SELL, D("1"),
                         D("3.20"), [], D("100000"))

    assert decision.blocked
    assert "unbounded" in decision.reason


def test_selling_an_option_you_hold_is_still_allowed(firm_record):
    """Closing is stopping the bleeding, and is never blocked."""
    rm = RiskManager()
    held = [Position(firm_id=firm_record.id, symbol=CALL,
                     quantity=D("2"), avg_price=D("3.00"))]
    decision = rm.review(_firm(firm_record), CALL, Side.SELL, D("2"),
                         D("4.00"), held, D("100000"))

    assert decision.verdict == RiskVerdict.ALLOW.value


# =========================================================================
# expiry: the day the position stops existing
# =========================================================================
def _open(store, firm, symbol, quantity="2", price="3.00"):
    store.settle(store.require_firm_by_id(firm.id),
                 Fill(firm_id=firm.id, symbol=symbol, side="buy",
                      quantity=D(quantity), price=D(price)))


def test_an_out_of_the_money_call_expires_worthless(store, firm_record):
    _open(store, firm_record, CALL, price="3.00")          # paid $600
    market = _Market(spot="430")                            # 450 strike, OTM

    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, market.as_of())

    [done] = report.settled
    assert done.expired_worthless
    assert done.intrinsic == Decimal("0.00")
    assert done.realized == Decimal("-600.00")             # the whole premium
    assert not [p for p in store.positions(firm_record.id) if p.is_open]


def test_an_in_the_money_call_settles_at_intrinsic(store, firm_record):
    _open(store, firm_record, CALL, price="3.00")          # paid $600
    market = _Market(spot="470")                            # $20 in the money

    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, market.as_of())

    [done] = report.settled
    assert done.intrinsic == Decimal("20.00")
    assert done.proceeds == Decimal("4000.00")             # $20 x 2 x 100
    assert done.realized == Decimal("3400.00")             # less the $600 paid
    assert not [p for p in store.positions(firm_record.id) if p.is_open]


def test_an_in_the_money_put_settles_the_other_way(store, firm_record):
    _open(store, firm_record, PUT, price="2.00")
    market = _Market(spot="430")                            # 450 strike put, ITM

    [done] = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, market.as_of()).settled
    assert done.intrinsic == Decimal("20.00")
    assert done.realized == Decimal("3600.00")


def test_an_unpriceable_underlying_is_refused_not_settled_at_zero(store, firm_record):
    """A contract nobody could price might have been worth a fortune.

    Settling it at zero books a total loss that looks exactly like a strategy
    that failed, on a position whose value was simply unknown.
    """
    _open(store, firm_record, CALL)
    market = _Market(missing=True)

    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, market.as_of())

    assert report.settled == []
    [refused] = report.refused
    assert "could not be priced" in refused.refused
    assert [p for p in store.positions(firm_record.id) if p.is_open], "still held"


def test_a_contract_that_has_not_expired_is_left_alone(store, firm_record):
    _open(store, firm_record, CALL)
    market = _Market()

    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert not report.any
    assert [p for p in store.positions(firm_record.id) if p.is_open]


def test_stocks_are_never_touched_by_expiry(store, firm_record):
    _open(store, firm_record, "SPY", quantity="10", price="100")
    market = _Market()

    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                market, market.as_of())
    assert not report.any
    assert [p for p in store.positions(firm_record.id) if p.is_open]


def test_the_books_still_reconcile_across_an_expiry(store, firm_record, market,
                                                    trading_config):
    """The identity that makes every other figure trustworthy.

    Expiry writes a fill like any other, which is the whole reason it does not
    need to know what the identities are.
    """
    from src.trading.brokerage.reconciliation import Reconciler

    _open(store, firm_record, CALL, price="3.00")
    report = expiry.settle_firm(store, store.require_firm_by_id(firm_record.id),
                                _Market(spot="470"), _Market().as_of())
    assert report.settled

    check = Reconciler(store, trading_config.brokerage).check(
        [store.require_firm_by_id(firm_record.id)], market)
    assert check.ok, check.summary()


# =========================================================================
# what this deliberately does not model
# =========================================================================
def test_cash_settlement_is_declared_rather_than_implied():
    """Real options are assigned into shares, which costs strike x 100 of
    capital a firm may not have. Cash settlement gets the P&L right and the
    capital requirement wrong, in the flattering direction."""
    assert expiry.SETTLES_FOR_CASH is True
    assert "assigned" in expiry.__doc__


def test_expiry_still_holds_no_pricing_model():
    """Intrinsic value is the strike, the spot and a subtraction. Anything
    that needed a volatility input would be a model price wearing a mark."""
    contract = options.parse(CALL)
    assert contract.intrinsic(D("470")) == Decimal("20.00")
    assert contract.intrinsic(D("430")) == Decimal("0.00")
    assert contract.expiry == date(2026, 9, 18)

    source = (expiry.__file__).replace(".py", ".py")
    with open(source) as fh:
        text = fh.read().lower()
    for banned in ("black_scholes", "implied_vol", "def delta", "def theta"):
        assert banned not in text, f"{banned} has no business here"


@pytest.mark.parametrize("symbol,expected", [
    (CALL, 100), (PUT, 100), ("SPY", 1), ("", 1), ("NOT-AN-OPTION", 1),
])
def test_contract_size_never_guesses(symbol, expected):
    assert options.contract_size(symbol) == expected
