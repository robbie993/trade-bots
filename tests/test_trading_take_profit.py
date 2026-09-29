"""The exit genes: ways to leave a winner other than the analysts turning.

Every one is off at zero, so a firm that does not name them trades exactly as
before. These pin what each does when it is on.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading.firms.firm import Firm


def _firm(firm_record, **genes):
    firm_record.genome = dict(firm_record.genome or {}, **genes)
    return Firm(firm_record)


def _hold(store, firm_record, symbol, avg_price, quantity="10"):
    store.db.insert("positions", {"firm_id": firm_record.id, "symbol": symbol,
                                  "quantity": quantity, "avg_price": str(avg_price)})
    return store.positions(firm_record.id)


def _taken(proposals):
    return [p for p in proposals if (p.rationale or "").startswith("TAKE PROFIT")]


def test_no_exit_genes_no_take_profits(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / 2)          # up 100%
    assert not _taken(_firm(firm_record).propose(market_data, positions))


def test_a_fixed_target_sells_the_whole_position(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / Decimal("1.2"))   # up 20%
    (p,) = _taken(_firm(firm_record, tp_pct=15).propose(market_data, positions))
    assert p.symbol == "SPY" and p.side == "sell" and p.quantity == Decimal("10")
    assert not _taken(_firm(firm_record, tp_pct=25).propose(market_data, positions))


def test_a_scale_out_sells_its_fraction_once(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / Decimal("1.1"))   # up 10%
    firm = _firm(firm_record, tp_scale_pct=6, tp_scale_frac=0.5)
    (p,) = _taken(firm.propose(market_data, positions))
    assert p.quantity == Decimal("5")
    assert not _taken(firm.propose(market_data, positions)), "sold half again"


def test_a_trail_waits_for_the_giveback(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / Decimal("1.1"))   # up 10%
    firm = _firm(firm_record, trail_arm_pct=5, trail_pct=3)
    assert not _taken(firm.propose(market_data, positions)), "no giveback yet"
    firm.exit_memory["SPY"]["peak"] = str(mark * Decimal("1.05"))       # it was higher
    (p,) = _taken(firm.propose(market_data, positions))
    assert "gave back" in p.rationale


def test_breakeven_keeps_a_winner_from_becoming_a_loss(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark)                   # flat now
    firm = _firm(firm_record, breakeven_arm_pct=4)
    assert not _taken(firm.propose(market_data, positions)), "never ran, nothing to protect"
    firm.exit_memory["SPY"]["peak"] = str(mark * Decimal("1.06"))       # it ran 6%
    (p,) = _taken(firm.propose(market_data, positions))
    assert "came back to cost" in p.rationale


def test_a_ridden_winner_is_not_sold_on_the_analysts_say_so(store, firm_record, market_data,
                                                             monkeypatch):
    from src.trading.models import TradeProposal

    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / Decimal("1.05"))  # up 5%
    sell = TradeProposal(firm_id=firm_record.id, symbol="SPY", side="sell",
                         quantity=Decimal("10"), reference_price=mark, rationale="bear won")
    riding = _firm(firm_record, ride_pct=3)
    monkeypatch.setattr(riding, "_from_pod", lambda *a: [sell])
    assert not [p for p in riding.propose(market_data, positions) if p.side == "sell"]
    plain = _firm(firm_record, ride_pct=0)
    monkeypatch.setattr(plain, "_from_pod", lambda *a: [sell])
    assert [p for p in plain.propose(market_data, positions) if p.side == "sell"]


def test_memory_is_dropped_when_the_position_is(store, firm_record, market_data):
    mark = market_data.mark("SPY")
    positions = _hold(store, firm_record, "SPY", mark / Decimal("1.1"))
    firm = _firm(firm_record, trail_arm_pct=5, trail_pct=3)
    firm.propose(market_data, positions)
    assert "SPY" in firm.exit_memory
    firm.propose(market_data, [])
    assert "SPY" not in firm.exit_memory
