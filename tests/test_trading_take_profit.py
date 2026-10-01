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


# -- a restart (every deploy) must not forget how far a winner ran -----------

def _bought(store, firm_record, symbol, bar, side="buy"):
    from src.db.connection import to_iso

    store.db.insert("fills", {"firm_id": firm_record.id, "symbol": symbol, "side": side,
                              "quantity": "10", "price": str(bar.close),
                              "as_of": to_iso(bar.as_of)})


def _a_run_then_back(market_data):
    """A holding, and the bars since its buy, that ran above today's price and came back."""
    for cursor in range(150, 40, -1):
        market_data.seek(cursor)
        for symbol in ("SPY", "QQQ"):
            mark = market_data.mark(symbol)
            for lookback in range(5, 40):
                bars = market_data.history(symbol, lookback)
                best = max(b.close for b in bars)
                if best > mark * Decimal("1.003"):
                    return symbol, bars, best, mark
    raise AssertionError("the synthetic feed never ran above a later price")


def test_a_restart_remembers_how_far_a_winner_ran(ecosystem, store, firm_record, market_data):
    symbol, bars, best, mark = _a_run_then_back(market_data)
    _bought(store, firm_record, symbol, bars[0])
    positions = _hold(store, firm_record, symbol, mark)                # flat now
    arm = (best - mark) / mark * Decimal(50)                          # half the run, in %

    forgetful = _firm(firm_record, breakeven_arm_pct=arm)              # the dict a restart leaves
    assert not _taken(forgetful.propose(market_data, positions)), "it thinks it never ran"

    recalls = _firm(firm_record, breakeven_arm_pct=arm)
    recalls.recall_exit = lambda s, m: ecosystem._recall_exit(firm_record.id, s, m)
    (p,) = _taken(recalls.propose(market_data, positions))
    assert "came back to cost" in p.rationale
    assert Decimal(recalls.exit_memory[symbol]["peak"]) == best


def test_the_recalled_peak_starts_at_the_last_buy_not_the_first(ecosystem, store, firm_record,
                                                                 market_data):
    symbol, bars, _, _ = _a_run_then_back(market_data)
    _bought(store, firm_record, symbol, bars[0])
    _bought(store, firm_record, symbol, bars[-1])                     # averaged in just now
    recalled = ecosystem._recall_exit(firm_record.id, symbol, market_data)
    assert recalled["peak"] == bars[-1].close and not recalled["scaled"]


def test_a_sell_since_the_buy_is_a_scale_out_already_taken(ecosystem, store, firm_record,
                                                           market_data):
    symbol, bars, _, _ = _a_run_then_back(market_data)
    _bought(store, firm_record, symbol, bars[0])
    _bought(store, firm_record, symbol, bars[1], side="sell")
    assert ecosystem._recall_exit(firm_record.id, symbol, market_data)["scaled"] is True


def test_nothing_in_the_ledger_means_nothing_recalled(ecosystem, firm_record, market_data):
    assert ecosystem._recall_exit(firm_record.id, "SPY", market_data) == {}


def test_every_live_firm_can_recall(ecosystem):
    for record in ecosystem.store.active_firms():
        assert ecosystem.build_firm(record).recall_exit is not None


def test_the_take_profit_firm_and_its_control_differ_only_in_the_exits():
    """The twin is the comparison: anything else that differs muddies it."""
    from pathlib import Path

    from src.trading.firms.spec import load_firm_specs

    specs = {s.firm_key: s for s in load_firm_specs(
        path=Path(__file__).resolve().parent.parent / "config" / "firm_config.yaml")}
    tp, ctl = specs["firm_e_momentum_tp"], specs["firm_e_momentum_ctl"]
    for field in ("asset_class", "strategy", "risk_limit", "allocation", "universe", "analysts",
                  "venue"):
        assert getattr(tp, field) == getattr(ctl, field), field
    exits = set(Firm.EXIT_GENES)
    assert {k: v for k, v in tp.genome.items() if k not in exits} == ctl.genome
    assert not exits & set(ctl.genome), "the control takes no profit"
    assert exits & set(tp.genome), "and the take-profit firm does"
