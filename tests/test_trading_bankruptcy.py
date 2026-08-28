"""Bankruptcy: a dead firm sells what it holds, and the estate is closed.

The behaviour under test replaces the arrangement that let six firms sit dead
for up to thirteen days holding $155,859 they were forbidden to sell. Two
separate gates caused that, and both are covered here, because fixing either
one alone leaves the money exactly where it was:

* `Firm.propose` returned `[]` for a killed firm, so it never asked to sell.
* `Conscience._authority` blocked every order from a killed firm, so on the
  ticks it did ask, it was refused.
"""

from decimal import Decimal

import pytest

from src.money import D
from src.trading.firms.firm import Firm
from src.trading.firms import bankruptcy
from src.trading.models import FirmRecord, FirmStatus, Position, Side, TradeProposal


def make_firm(**overrides):
    defaults = dict(
        firm_key="f",
        allocation=Decimal("100000"),
        initial_allocation=Decimal("100000"),
        cash=Decimal("100000"),
        risk_limit=Decimal("0.02"),
        universe=["SPY"],
        id=1,
    )
    defaults.update(overrides)
    return FirmRecord(**defaults)


# =========================================================================
# the estate proposes its own exits
# =========================================================================
def test_a_killed_firm_with_a_book_proposes_to_sell_all_of_it(market, trading_config):
    market.seek(150)
    record = make_firm(universe=["SPY", "QQQ"], status="killed",
                       kill_reason="6 consecutive losing trades")
    firm = Firm(record, limits=trading_config.firm)
    held = [
        Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100)),
        Position(firm_id=1, symbol="QQQ", quantity=D(4), avg_price=D(200)),
    ]
    proposals = firm.propose(market, held)

    assert {p.symbol for p in proposals} == {"SPY", "QQQ"}
    assert all(p.side == Side.SELL.value for p in proposals)
    # Whole positions. An estate is ending an exit, not managing one.
    assert {p.symbol: p.quantity for p in proposals} == {"SPY": D(10), "QQQ": D(4)}
    assert all("BANKRUPTCY" in p.rationale for p in proposals)


def test_a_killed_firm_holding_nothing_proposes_nothing(market, trading_config):
    market.seek(150)
    record = make_firm(universe=["SPY"], status="killed")
    firm = Firm(record, limits=trading_config.firm)
    assert firm.propose(market, []) == []


def test_a_wound_up_firm_proposes_nothing_even_holding_something(market, trading_config):
    """BANKRUPT is terminal. Nothing reopens an estate that has been settled."""
    market.seek(150)
    record = make_firm(universe=["SPY"], status=FirmStatus.BANKRUPT.value)
    firm = Firm(record, limits=trading_config.firm)
    held = [Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100))]
    assert firm.propose(market, held) == []


def test_an_unpriceable_holding_is_not_dumped_at_a_guess(market, trading_config):
    """No mark, no sale — the firm stays in liquidation and says so."""
    market.seek(150)
    record = make_firm(universe=["SPY", "QQQ"], status="killed")
    firm = Firm(record, limits=trading_config.firm)
    market.unpriceable["QQQ"] = "the feed cannot price it"
    held = [
        Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100)),
        Position(firm_id=1, symbol="QQQ", quantity=D(4), avg_price=D(200)),
    ]
    assert {p.symbol for p in firm.propose(market, held)} == {"SPY"}


# =========================================================================
# the conscience lets the estate out
# =========================================================================
def test_the_conscience_lets_a_dead_firm_close_and_stops_it_opening(
    store, trading_config, market_data
):
    from src.trading.heart.conscience import Conscience

    conscience = Conscience(trading_config.heart)
    record = make_firm(status="killed", universe=["SPY"])
    held = [Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100))]

    closing = TradeProposal(firm_id=1, symbol="SPY", side=Side.SELL.value,
                            quantity=D(10), reference_price=D(100))
    opening = TradeProposal(firm_id=1, symbol="SPY", side=Side.BUY.value,
                            quantity=D(10), reference_price=D(100))

    out = conscience._authority(closing, record, held)
    assert out.verdict != "block", f"the way out must never be a gate: {out.detail}"

    assert conscience._authority(opening, record, held).verdict == "block"


# =========================================================================
# winding up
# =========================================================================
def _kill_with_a_book(eco, firm_key="alpha"):
    firm = eco.store.get_firm(firm_key)
    eco.store.set_firm_status(firm.id, FirmStatus.KILLED.value, "test kill")
    return eco.store.require_firm_by_id(firm.id)


def test_wind_up_refuses_a_firm_that_still_holds_something(ecosystem):
    eco = ecosystem
    firm = _kill_with_a_book(eco)
    eco.store._save_position(
        Position(firm_id=firm.id, symbol="SPY", quantity=D(5), avg_price=D(100))
    )
    assert bankruptcy.wind_up(eco, eco.store.require_firm_by_id(firm.id)) is None
    assert eco.store.require_firm_by_id(firm.id).status == FirmStatus.KILLED.value


def test_wind_up_refuses_a_living_firm(ecosystem):
    eco = ecosystem
    firm = eco.store.firms()[0]
    assert bankruptcy.wind_up(eco, firm) is None


def test_wind_up_returns_the_cash_writes_a_lesson_and_files_an_heir(ecosystem):
    eco = ecosystem
    firm = _kill_with_a_book(eco)
    before = D(firm.cash)
    assert before > 0, "fixture should fund the firm, or this proves nothing"

    closed = bankruptcy.wind_up(eco, firm)
    assert closed is not None

    settled = eco.store.require_firm_by_id(firm.id)
    assert settled.status == FirmStatus.BANKRUPT.value
    assert settled.is_bankrupt and settled.is_killed  # dead by either name
    assert D(settled.cash) == 0, "the capital must not stay in a dead firm"

    lessons = [m for m in eco.memory.recall(firm_id=firm.id)
               if m.memory_type == "bankruptcy"]
    assert len(lessons) == 1
    assert firm.firm_key in lessons[0].summary

    heir = eco.store.get_firm(closed["successor"])
    assert heir is not None
    assert D(heir.allocation) == 0, "an heir must be born broke"
    assert heir.genome.get("inherited_from") == firm.firm_key
    assert heir.genome.get("inherited_lesson")


def test_wind_up_is_idempotent(ecosystem):
    """The tick calls this on every dead firm on every pass."""
    eco = ecosystem
    firm = _kill_with_a_book(eco)
    assert bankruptcy.wind_up(eco, firm) is not None

    settled = eco.store.require_firm_by_id(firm.id)
    assert bankruptcy.wind_up(eco, settled) is None

    heirs = [f for f in eco.store.firms() if f.firm_key.startswith(firm.firm_key + "_")]
    assert len(heirs) == 1, "a second pass must not breed a second heir"
