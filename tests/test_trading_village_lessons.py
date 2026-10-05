"""Five lessons from the other trading villages surveyed on 2026-10-05.

* a new position is refused when the symbol's typical move cannot pay for it;
* a firm on a losing run bets smaller;
* a symbol swinging harder than usual gets a smaller position;
* a firm sitting where two or more others already sit loses score points;
* the firms' advisers never see the ranking.
"""

import math
from decimal import Decimal as D
from types import SimpleNamespace


class _Market:
    def __init__(self, closes):
        self._closes = closes

    def closes(self, symbol, lookback=None):
        return [D(str(c)) for c in self._closes]

    def mark(self, symbol):
        return D(str(self._closes[-1]))


def _firm(losses=0, cost=None):
    from src.trading.config import FirmDefaults
    from src.trading.firms.firm import Firm
    from src.trading.models import FirmRecord

    record = FirmRecord(id=1, firm_key="f", name="f", allocation=D("10000"),
                        cash=D("10000"), consecutive_losses=losses)
    firm = Firm(record=record, analysts=[], limits=FirmDefaults())
    firm.round_trip_cost = cost
    return firm


def _buy(n="10"):
    from src.trading.models import TradeProposal

    return TradeProposal(firm_id=1, symbol="X", side="buy", quantity=D(n),
                         reference_price=D("100"), notional=D(n) * 100, rationale="r")


def _wave(n=300, amp=0.03, period=20, calm_tail=0):
    return [100 * (1 + amp * math.sin(i * 2 * math.pi / period)) for i in range(n)]


def test_a_move_too_small_to_pay_the_fees_is_refused():
    flat = [100 + 0.01 * (i % 2) for i in range(300)]
    why = _firm(cost=D("0.005"))._lessons(_buy(), _Market(flat))
    assert why.startswith("edge below costs")


def test_a_move_that_pays_the_fees_goes_through_unchanged():
    p = _buy()
    assert _firm(cost=D("0.001"))._lessons(p, _Market(_wave())) == ""
    assert p.quantity == D("10")


def test_no_known_cost_means_no_edge_rule():
    flat = [100 + 0.01 * (i % 2) for i in range(300)]
    assert _firm(cost=None)._lessons(_buy(), _Market(flat)) == ""


def test_a_losing_run_shrinks_the_bet_down_to_a_quarter():
    p = _buy()
    _firm(losses=2)._lessons(p, _Market(_wave()))
    assert p.quantity == D("7") and "2 loss(es) in a row" in p.rationale
    p = _buy()
    _firm(losses=9)._lessons(p, _Market(_wave()))
    assert p.quantity == D("2.5")


def test_a_symbol_swinging_harder_than_usual_gets_a_smaller_bet():
    calm = [100 * (1 + 0.005 * math.sin(i)) for i in range(280)]
    wild = [100 * (1 + 0.04 * math.sin(i)) for i in range(20)]
    p = _buy()
    _firm()._lessons(p, _Market(calm + wild))
    assert p.quantity < D("10") and "swinging" in p.rationale


def test_exits_are_never_shrunk_or_refused():
    from src.trading.models import TradeProposal

    firm = _firm(losses=9, cost=D("0.5"))
    sell = TradeProposal(firm_id=1, symbol="X", side="sell", quantity=D("10"))
    out = firm._review_all([sell], _Market([100.0] * 300), [], D("10000"))
    assert all("edge below costs" not in (o.risk_reason or "")
               and "loss(es)" not in (o.rationale or "") for o in out)


def _crowd_store(books):
    """books: {firm_id: [symbol, ...]} for active firms."""
    def positions(fid, open_only=True):
        return [SimpleNamespace(symbol=s, is_open=True) for s in books.get(fid, [])]
    firms = [SimpleNamespace(id=f) for f in books]
    return SimpleNamespace(active_firms=lambda: firms, positions=positions)


def _pos(symbol, value):
    return SimpleNamespace(symbol=symbol, market_value=lambda mark, v=value: D(v))


def test_a_book_where_the_village_already_sits_is_crowded():
    from src.trading.brokerage.evaluator import Evaluator

    ev = Evaluator(_crowd_store({1: [], 2: ["NVDA", "AAPL"], 3: ["NVDA"], 4: ["AAPL"]}))
    me = SimpleNamespace(id=1)
    mine = [_pos("NVDA", 3000), _pos("XOM", 1000)]
    assert ev._crowded_pct(me, mine, _Market([100.0])) == D("75")


def test_crowding_costs_points_and_being_different_costs_none():
    from src.trading.brokerage.evaluator import Evaluator, Scorecard

    ev = Evaluator(store=None)
    alone, _ = ev.score(Scorecard(firm_key="a", firm_id=1, crowded_pct=D("0")))
    herd, parts = ev.score(Scorecard(firm_key="b", firm_id=2, crowded_pct=D("100")))
    assert herd == alone - D(10) and D(parts["crowding"]) == D(-10)


def test_the_advisers_never_see_the_ranking():
    from src.trading.ask import _unranked

    ctx = {"score": 55, "rank": 3, "leaderboard": [1, 2],
           "nested": {"tokens": 900, "kept": 1}, "papers": [{"title": "t", "standings": 1}]}
    assert _unranked(ctx) == {"score": 55, "nested": {"kept": 1}, "papers": [{"title": "t"}]}


def test_the_stakes_no_longer_promise_leaderboard_titles():
    from src.trading.ask import _stakes

    db = SimpleNamespace(query_one=lambda *a, **k: None)
    s = _stakes(db, SimpleNamespace(id=1))
    assert "leaderboard" not in s["reward"] and "token" not in s["reward"]
