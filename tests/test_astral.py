"""ASTRAL: volatility rotation in a correlated basket, scaled on volume.

Driven with hand-built baskets rather than the synthetic feed, because each
rule needs a specific market to show up in: a correlated group with one name
leading on heavy volume, a group that has stopped moving together, a leader
being sold into.
"""

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.trading import adapter
from src.trading.adapter import Context, Holding

BOT = "bots/astral.py"
BARS = 250
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")


@pytest.fixture(scope="module")
def propose():
    return adapter.load(BOT)


def basket(drifts=None, correlated=True, surge=None, last_bar=None,
           volume=True, seed=7):
    """Bars for five names.

    ``drifts`` adds a per-bar return to a name over the last 16 bars (the
    momentum window). ``surge`` names get 3x volume on the last bar.
    ``last_bar`` forces the sign of each name's last return.
    """
    drifts = drifts or {}
    surge = surge or ()
    last_bar = last_bar or {}
    rng = random.Random(seed)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=15 * i) for i in range(BARS)]
    common = [rng.gauss(0, 0.003) for _ in range(BARS)]

    out = {}
    for symbol in SYMBOLS:
        price, opens, closes, highs, lows, vols = 100.0, [], [], [], [], []
        for i in range(BARS):
            late = i >= BARS - 16
            r = (common[i] if correlated else rng.gauss(0, 0.003)) + rng.gauss(0, 0.001)
            if late:
                r += 0.0005 + drifts.get(symbol, 0.0)
                if symbol in drifts and i >= BARS - 8:
                    r += rng.gauss(0, 0.006)       # the leader's vol expands
            if i == BARS - 1 and symbol in last_bar:
                r = abs(r) * last_bar[symbol] or 0.002 * last_bar[symbol]
            opened = price
            price *= 1 + r
            opens.append(opened)
            closes.append(price)
            highs.append(max(opened, price) * 1.001)
            lows.append(min(opened, price) * 0.999)
            vols.append(1000 + rng.randrange(0, 200))
        if symbol in surge:
            vols[-1] = 3500
        out[symbol] = dict(opens=opens, closes=closes, highs=highs, lows=lows,
                           volumes=vols if volume else [], times=times)
    return out


def context(bars, held=None, equity=100000):
    held = held or {}
    d = lambda xs: [Decimal(str(x)) for x in xs]  # noqa: E731
    return Context(
        universe=SYMBOLS,
        cash=Decimal(equity),
        equity=Decimal(equity),
        as_of=bars[SYMBOLS[0]]["times"][-1],
        _closes={s: d(b["closes"]) for s, b in bars.items()},
        _marks={s: Decimal(str(b["closes"][-1])) for s, b in bars.items()},
        _positions={s: Holding(s, Decimal(q), Decimal(str(bars[s]["closes"][-1])))
                    for s, q in held.items()},
        _highs={s: d(b["highs"]) for s, b in bars.items()},
        _lows={s: d(b["lows"]) for s, b in bars.items()},
        _opens={s: d(b["opens"]) for s, b in bars.items()},
        _volumes={s: d(b["volumes"]) for s, b in bars.items()},
        _times={s: b["times"] for s, b in bars.items()},
    )


def test_it_opens_the_leader_that_moves_on_volume(propose):
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1})
    orders = propose(context(bars))
    assert [o["symbol"] for o in orders] == ["AAA"], orders
    order = orders[0]
    assert order["side"] == "buy"
    assert "opening" in order["rationale"]
    # One or two tranches of a 40% slot, never the whole slot at once.
    assert 0 < order["notional"] <= Decimal("100000") * Decimal("0.40") * 2 / 3


def test_a_leader_without_volume_gets_no_size(propose):
    bars = basket(drifts={"AAA": 0.004}, last_bar={"AAA": 1})
    assert propose(context(bars)) == []


def test_a_feed_without_volume_never_opens_anything(propose):
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1},
                  volume=False)
    assert propose(context(bars)) == []


def test_a_basket_that_stopped_moving_together_opens_nothing(propose):
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1},
                  correlated=False)
    assert [o for o in propose(context(bars)) if o["side"] == "buy"] == []


def test_a_held_name_that_lost_the_lead_is_rotated_out(propose):
    bars = basket(drifts={"AAA": 0.004, "BBB": 0.004, "DDD": 0.0008})
    orders = propose(context(bars, held={"DDD": 300}))
    sells = [o for o in orders if o["side"] == "sell"]
    assert [o["symbol"] for o in sells] == ["DDD"], orders
    assert "rotating out" in sells[0]["rationale"]
    assert sells[0]["quantity"] < Decimal(300), "a tranche at a time, not all at once"


def test_heavy_selling_into_a_leader_cuts_one_tranche(propose):
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": -1})
    orders = propose(context(bars, held={"AAA": 350}))
    assert len(orders) == 1, orders
    assert orders[0]["side"] == "sell"
    assert "distribution" in orders[0]["rationale"]
    assert 0 < orders[0]["quantity"] < Decimal(350)


def test_a_name_whose_momentum_turned_is_sold_outright(propose):
    bars = basket(drifts={"AAA": -0.004})
    orders = propose(context(bars, held={"AAA": 100}))
    assert orders == [o for o in orders if o["symbol"] == "AAA"]
    assert orders[0]["quantity"] == Decimal(100)


def test_it_declines_without_enough_history(propose):
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1})
    for series in bars.values():
        for key in series:
            series[key] = series[key][-40:]
    assert propose(context(bars)) == []


def test_ten_five_name_baskets_with_no_name_in_two():
    import importlib.util

    spec = importlib.util.spec_from_file_location("astral_bot", BOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert len(module.BASKETS) == 10
    assert all(len(names) == 5 for names in module.BASKETS.values())
    names = [sym for names in module.BASKETS.values() for sym in names]
    assert len(names) == len(set(names))


def test_astral_is_not_in_the_village_yet():
    """Held out on purpose until it is ready. Wiring a firm to it is a
    decision, and this test is where that decision gets noticed."""
    from src.trading.firms.spec import load_firm_specs

    assert not [s.firm_key for s in load_firm_specs() if s.strategy == f"bot:{BOT}"]
