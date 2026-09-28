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


def context(bars, held=None, equity=100000, entries=None):
    held = held or {}
    entries = entries or {}
    d = lambda xs: [Decimal(str(x)) for x in xs]  # noqa: E731
    return Context(
        universe=SYMBOLS,
        cash=Decimal(equity),
        equity=Decimal(equity),
        as_of=bars[SYMBOLS[0]]["times"][-1],
        _closes={s: d(b["closes"]) for s, b in bars.items()},
        _marks={s: Decimal(str(b["closes"][-1])) for s, b in bars.items()},
        _positions={s: Holding(s, Decimal(q),
                               Decimal(str(entries.get(s, bars[s]["closes"][-1]))))
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


# =========================================================================
# the take-profit iterations
# =========================================================================
from pathlib import Path  # noqa: E402

VARIANTS = sorted(str(p).replace("\\", "/") for p in Path("bots").glob("astral_tp_*.py"))


@pytest.fixture(scope="module")
def core():
    from bots import astral

    return astral


def test_there_are_ten_iterations():
    assert len(VARIANTS) == 10, VARIANTS


@pytest.mark.parametrize("path", VARIANTS)
def test_each_iteration_runs_and_is_safe_to_recruit(path):
    from src.trading.court.evidence import gather

    evidence = gather(Path(path))
    assert evidence.syntax_error is None and evidence.is_safe, evidence.dangerous_imports
    bot = adapter.load(path)
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1})
    assert isinstance(bot(context(bars)), list)
    assert isinstance(bot(context(bars, held={"AAA": 100})), list)


@pytest.mark.parametrize("path", VARIANTS)
def test_every_target_is_far_above_the_entry(path, core):
    """At least 2R (6 ATR) out, whatever the idea says."""
    module = adapter.load(path).__globals__
    bars = basket(drifts={"AAA": 0.004}, surge=("AAA",), last_bar={"AAA": 1})
    entry = bars["AAA"]["closes"][-1]
    ctx = context(bars, held={"AAA": 100})
    read = core._read(ctx, "AAA")
    live = {s: core._read(ctx, s) for s in SYMBOLS}
    ladder = core._ladder(ctx, "AAA", read, live, module["targets"])
    assert ladder, "every idea has an answer on a healthy basket"
    floor = entry * (1 + core.MIN_TARGET_R * core.STOP_ATRS * read["atr_pct"])
    assert all(level >= floor * 0.999999 for level, _ in ladder)
    assert ladder[-1][1] == 0.0, "the last level closes the trade"


def test_plain_astral_has_no_target(core):
    bars = basket(drifts={"AAA": 0.004})
    ctx = context(bars, held={"AAA": 100})
    assert core._ladder(ctx, "AAA", core._read(ctx, "AAA"), {}, None) == []


def test_a_trade_past_its_target_is_closed(core):
    bars = basket(drifts={"AAA": 0.004})
    price = bars["AAA"]["closes"][-1]
    ctx = context(bars, held={"AAA": 100}, entries={"AAA": price / 1.3})
    orders = core.run(ctx, take_profit=lambda c, s, r, e, b: [(e * 1.01, 0.0)])
    sells = [o for o in orders if o["symbol"] == "AAA"]
    assert sells and sells[0]["side"] == "sell"
    assert sells[0]["quantity"] == Decimal(100)
    assert "take profit" in sells[0]["rationale"]


def test_a_tiered_target_sells_down_to_what_it_keeps(core):
    bars = basket(drifts={"AAA": 0.004})
    price = bars["AAA"]["closes"][-1]
    held = 350
    ctx = context(bars, held={"AAA": held}, entries={"AAA": price / 1.05})
    ladder = lambda c, s, r, e, b: [(e * 1.01, 2 / 3), (e * 100, 0.0)]  # noqa: E731
    orders = [o for o in core.run(ctx, take_profit=ladder) if o["symbol"] == "AAA"]
    assert len(orders) == 1 and orders[0]["side"] == "sell"
    assert 0 < orders[0]["quantity"] < Decimal(held)
    assert "keeping 67%" in orders[0]["rationale"]


def test_a_winner_short_of_its_target_is_not_rotated_out(core):
    """Plain Astral trims a name that lost the lead; an iteration lets a
    profitable one run to its target."""
    bars = basket(drifts={"AAA": 0.004, "BBB": 0.004, "DDD": 0.0008})
    price = bars["DDD"]["closes"][-1]
    ctx = context(bars, held={"DDD": 300}, entries={"DDD": price / 1.005})
    far = lambda c, s, r, e, b: [(e * 2, 0.0)]  # noqa: E731

    assert [o for o in core.run(ctx) if o["symbol"] == "DDD"], "plain Astral rotates"
    assert [o for o in core.run(ctx, take_profit=far) if o["symbol"] == "DDD"] == []


def test_a_losing_trade_still_rotates_out_under_a_target(core):
    bars = basket(drifts={"AAA": 0.004, "BBB": 0.004, "DDD": 0.0008})
    price = bars["DDD"]["closes"][-1]
    ctx = context(bars, held={"DDD": 300}, entries={"DDD": price * 1.002})
    far = lambda c, s, r, e, b: [(e * 2, 0.0)]  # noqa: E731
    sells = [o for o in core.run(ctx, take_profit=far) if o["symbol"] == "DDD"]
    assert sells and "rotating out" in sells[0]["rationale"]
