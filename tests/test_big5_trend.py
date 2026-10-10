"""Big-5 Trend: the frozen most-traded + QQQ 200-day rule from the Pelosi research.

The rule is decided on the last close of the previous month and held all month,
so these tests pin that: same targets on every tick of a month, cash when QQQ
is below its 200-day average, and nothing at all without enough history.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.trading import adapter
from src.trading.adapter import Context, Holding

BOT = adapter.load("bots/big5_trend.py")


def _days(n, end):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _ctx(qqq_up=True, held=(), end=datetime(2026, 10, 7, 20), n=250, volumes=None):
    times = _days(n, end)
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]
    vols = volumes or {s: Decimal(1000 * (len(names) - k)) for k, s in enumerate(names)}
    closes, vol, tm = {}, {}, {}
    step = Decimal("0.5") if qqq_up else Decimal("-0.5")
    closes["QQQ"] = [Decimal(300) + step * i for i in range(n)]
    vol["QQQ"] = [Decimal(1)] * n
    tm["QQQ"] = times
    for s in names:
        closes[s] = [Decimal(100)] * n
        vol[s] = [vols[s]] * n
        tm[s] = times
    positions = {s: Holding(symbol=s, quantity=Decimal(10), average_price=Decimal(100)) for s in held}
    return Context(universe=tuple(names + ["QQQ"]), cash=Decimal(20000), equity=Decimal(20000),
                   as_of=end, _closes=closes, _marks={}, _positions=positions,
                   _volumes=vol, _times=tm)


def test_buys_the_five_most_traded_at_twenty_percent():
    orders = BOT(_ctx())
    assert [o["symbol"] for o in orders] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert all(o["side"] == "buy" and o["notional"] == Decimal(4000) for o in orders)


def test_never_buys_qqq_even_if_it_trades_most():
    ctx = _ctx()
    ctx._volumes["QQQ"] = [Decimal(10 ** 9)] * len(ctx._volumes["QQQ"])
    assert "QQQ" not in {o["symbol"] for o in BOT(ctx)}


def test_goes_to_cash_when_qqq_is_below_its_200_day_average():
    orders = BOT(_ctx(qqq_up=False, held=("AAA", "BBB")))
    assert {(o["symbol"], o["side"]) for o in orders} == {("AAA", "sell"), ("BBB", "sell")}


def test_sells_a_name_that_drops_out_and_keeps_the_rest():
    orders = BOT(_ctx(held=("AAA", "GGG")))
    sells = {o["symbol"] for o in orders if o["side"] == "sell"}
    buys = {o["symbol"] for o in orders if o["side"] == "buy"}
    assert sells == {"GGG"}
    assert buys == {"BBB", "CCC", "DDD", "EEE"}


def test_a_matching_book_proposes_nothing():
    assert BOT(_ctx(held=("AAA", "BBB", "CCC", "DDD", "EEE"))) == []


def test_ranking_ignores_this_months_bars():
    """A name that only became heavily traded this month waits for month end."""
    ctx = _ctx()
    times = ctx._times["GGG"]
    ctx._volumes["GGG"] = [Decimal(10 ** 9) if t.month == 10 and t.year == 2026 else Decimal(1)
                           for t in times]
    assert "GGG" not in {o["symbol"] for o in BOT(ctx)}


def test_declines_without_two_hundred_days_before_the_month():
    assert BOT(_ctx(n=150)) == []
