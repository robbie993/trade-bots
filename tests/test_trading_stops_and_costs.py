"""The two things Robbie's fleet dossiers found wrong with this village.

Both came from outside — nine production bots were translated to run here, and
the translation notes said what the village could not express. Two of those
were not translation losses at all, they were holes:

* `context` never exposed what a position cost, so no imported bot could
  implement the stop it was written with;
* costs were one global number, so a firm on a venue charging 0.6% a side was
  billed 0.05% like an ETF desk — flattering it by an order of magnitude, on
  strategies whose entire deficit is smaller than their fees.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading.config import DataConfig
from src.trading.firms.spec import FirmSpec, FirmSpecError


# =========================================================================
# what a position cost — the number a stop needs
# =========================================================================
def _ctx(store, firm, market):
    from src.trading import adapter

    return adapter.build_context(firm, market, store.positions(firm.id),
                                 equity=Decimal("100000"))


def test_a_bot_can_finally_see_what_it_paid(store, firm_record, market_data):
    from src.trading import adapter

    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "SPY",
                                  "quantity": "10", "avg_price": "400"})
    ctx = adapter.build_context(firm_record, market_data,
                              store.positions(firm_record.id),
                              equity=Decimal("100000"))
    assert ctx.entry("SPY") == Decimal("400")
    assert ctx.entry("QQQ") is None, "no position, no entry — not zero"


def test_unknown_is_none_and_never_zero(store, firm_record, market_data):
    """Zero would read as a 100% loss and trip every stop in the fleet."""
    from src.trading import adapter

    ctx = adapter.build_context(firm_record, market_data, [],
                                equity=Decimal("100000"))
    assert ctx.entry("SPY") is None
    assert ctx.unrealized_pct("SPY") is None


def test_a_short_that_falls_is_up(store, firm_record, market_data):
    """Signed for the direction actually held, or every short reads backwards."""
    from src.trading import adapter

    mark = market_data.mark("SPY")
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "SPY",
                                  "quantity": "-10", "avg_price": str(mark * 2)})
    ctx = adapter.build_context(firm_record, market_data,
                              store.positions(firm_record.id),
                              equity=Decimal("100000"))
    assert ctx.unrealized_pct("SPY") > 0, "sold high, price halved — that is a profit"


# =========================================================================
# the stop
# =========================================================================
def _firm_with_stop(store, firm_record, stop):
    from src.trading.firms.firm import Firm

    firm_record.genome = dict(firm_record.genome or {})
    firm_record.genome["stop_loss_pct"] = stop
    return Firm(firm_record)


def test_a_position_past_its_stop_is_closed_without_a_debate(store, firm_record,
                                                             market_data):
    """The analysts do not get a vote. That is the whole point — waiting for
    the bear case to win is how a loss becomes eight times a win."""
    mark = market_data.mark("SPY")
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "SPY",
                                  "quantity": "10", "avg_price": str(mark * 2)})
    firm = _firm_with_stop(store, firm_record, 8)

    proposals = firm.propose(market_data, store.positions(firm_record.id))
    stops = [p for p in proposals if "STOP" in (p.rationale or "")]
    assert stops, "a position down 50% was left on the books"
    assert stops[0].symbol == "SPY"
    assert stops[0].quantity == Decimal("10"), "a stop closes the whole position"


def test_a_stop_of_zero_is_off(store, firm_record, market_data):
    """What every firm did before this gene existed."""
    mark = market_data.mark("SPY")
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "SPY",
                                  "quantity": "10", "avg_price": str(mark * 5)})
    firm = _firm_with_stop(store, firm_record, 0)
    proposals = firm.propose(market_data, store.positions(firm_record.id))
    assert not [p for p in proposals if "STOP" in (p.rationale or "")]


def test_a_winner_is_not_stopped(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "SPY",
                                  "quantity": "10", "avg_price": str(mark / 2)})
    firm = _firm_with_stop(store, firm_record, 8)
    proposals = firm.propose(market_data, store.positions(firm_record.id))
    assert not [p for p in proposals if "STOP" in (p.rationale or "")]


def test_an_unpriceable_position_is_never_stopped(store, firm_record, market_data):
    """A symbol with no mark reads as a total loss. Selling on that is the
    blind-feed massacre with extra steps."""
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": "PEPE-USD",
                                  "quantity": "10", "avg_price": "400"})
    market_data.unpriceable["PEPE-USD"] = "no exchange lists it"
    firm = _firm_with_stop(store, firm_record, 8)
    proposals = firm.propose(market_data, store.positions(firm_record.id))
    assert not [p for p in proposals if p.symbol == "PEPE-USD"]


def test_the_stop_is_a_gene_evolution_can_tune(store, firm_record):
    """A crypto desk and a rates desk should not share a tolerance, and
    neither should be a number I picked."""
    from src.trading.brain.evolver import BASE_GENOME, GENES

    assert "stop_loss_pct" in GENES
    low, high, is_int = GENES["stop_loss_pct"]
    assert low == 0, "zero has to stay reachable — it means off"
    assert high <= 25 and not is_int
    # Off by default, because it was A/B'd and 8% lost money on this village:
    # $9,671 with the stop against $16,587 without, and a *worse* loss/win
    # ratio. A stop is not free — it realises drawdowns that would have
    # recovered — so the level belongs to evolution, not to me.
    assert BASE_GENOME["stop_loss_pct"] == 0


# =========================================================================
# what the venue really charges
# =========================================================================
def test_a_crypto_desk_is_not_billed_like_an_etf_desk():
    spec = FirmSpec.from_mapping("cb", {
        "universe": ["BTC-USD"], "costs": {"fee_bps": 60, "slippage_bps": 20}})
    data = DataConfig()
    assert data.fee_bps < Decimal(10), "the village default is an ETF default"

    costed = spec.costed(data)
    assert costed.fee_bps == Decimal(60)
    assert costed.slippage_bps == Decimal(20)


def test_a_firm_that_declares_nothing_keeps_the_village_default():
    spec = FirmSpec.from_mapping("etf", {"universe": ["SPY"]})
    data = DataConfig()
    assert spec.costs_overridden is False
    assert spec.costed(data) is data, "the common case should allocate nothing"


def test_one_side_can_be_overridden_without_the_other():
    spec = FirmSpec.from_mapping("x", {"universe": ["SPY"], "fee_bps": 60})
    data = DataConfig()
    assert spec.costed(data).fee_bps == Decimal(60)
    assert spec.costed(data).slippage_bps == data.slippage_bps


def test_nonsense_costs_are_refused_at_load_rather_than_at_the_venue():
    import pytest

    with pytest.raises(FirmSpecError, match="must be a number"):
        FirmSpec.from_mapping("x", {"universe": ["SPY"], "fee_bps": "free"})
    with pytest.raises(FirmSpecError, match="cannot be negative"):
        FirmSpec.from_mapping("x", {"universe": ["SPY"], "fee_bps": -5})


def test_the_costs_reach_the_venue_the_firm_actually_trades_on(ecosystem):
    """Wiring, not arithmetic: a config that never reaches `build_venue` is a
    setting that does nothing."""
    record = ecosystem.store.firms()[0]
    assert ecosystem._costed_for(record) is ecosystem.config

    ecosystem._specs[record.firm_key] = FirmSpec.from_mapping(
        record.firm_key, {"universe": ["SPY"], "costs": {"fee_bps": 60}})
    assert ecosystem._costed_for(record).data.fee_bps == Decimal(60)


def test_higher_costs_really_do_take_more_money(ecosystem):
    """The point of all of it. Same trade, two cost models, different bill."""
    from src.trading.execution import build_venue
    from src.trading.models import Side

    cheap = build_venue("paper", ecosystem.config, ecosystem.gate)
    record = ecosystem.store.firms()[0]
    ecosystem._specs[record.firm_key] = FirmSpec.from_mapping(
        record.firm_key, {"universe": ["SPY"], "costs": {"fee_bps": 60,
                                                         "slippage_bps": 20}})
    dear = build_venue("paper", ecosystem._costed_for(record), ecosystem.gate)

    a = cheap.quote(Side.BUY, Decimal("100"), Decimal("10"))
    b = dear.quote(Side.BUY, Decimal("100"), Decimal("10"))
    assert b.fee > a.fee
    assert b.fill_price > a.fill_price, "slippage works against the trader on a buy"
