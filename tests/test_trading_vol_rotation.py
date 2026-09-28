"""Volatility rotation on 15-minute bars, and the plumbing it needed.

Three things had to exist before a bot could do this at all: a bar length
other than a day, volume in the bot's context, and a backtest that runs the
firm's bot rather than the pod. Those are tested first, then the engine's
decisions one at a time on bars built to force each one.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.trading import adapter, vol_rotation
from src.trading.backtest import Backtester
from src.trading.config import DataConfig, TradingConfig
from src.trading.data.feeds import (
    FeedNotConfigured, SyntheticFeed, _venue_timeframe, build_feed, timeframe_minutes,
)
from src.trading.data.market_data import MarketData
from src.trading.firms.spec import load_firm_specs
from src.trading.models import Bar

REPO = Path(__file__).resolve().parent.parent
BOTS = sorted((REPO / "bots" / "vol_rotation").glob("*.py"))


# =========================================================================
# bar length
# =========================================================================
@pytest.mark.parametrize("text,minutes", [
    ("15m", 15), ("15min", 15), ("15Min", 15), ("1h", 60), ("4H", 240),
    ("1d", 1440), ("1Day", 1440), ("d", 1440),
])
def test_bar_lengths_are_read_the_way_people_write_them(text, minutes):
    assert timeframe_minutes(text) == minutes


@pytest.mark.parametrize("text", ["15x", "0m", "fortnightly"])
def test_a_bar_length_nobody_can_read_is_refused(text):
    with pytest.raises(FeedNotConfigured):
        timeframe_minutes(text)


def test_each_venue_gets_its_own_spelling():
    assert _venue_timeframe(15, "alpaca") == "15Min"
    assert _venue_timeframe(60, "alpaca") == "1Hour"
    assert _venue_timeframe(1440, "alpaca") == "1Day"
    assert _venue_timeframe(60, "yahoo") == "60m"
    assert _venue_timeframe(15, "ccxt") == "15m"
    assert _venue_timeframe(240, "ccxt") == "4h"


def test_daily_synthetic_bars_are_unchanged():
    """Every seeded backtest in the repo was run on these. They must not move."""
    daily = SyntheticFeed(seed=7, days=40).series("SPY")
    explicit = SyntheticFeed(seed=7, days=40, bar_minutes=1440).series("SPY")
    assert daily == explicit
    assert daily[1].as_of - daily[0].as_of == timedelta(days=1)


def test_synthetic_bars_step_by_the_configured_length():
    bars = SyntheticFeed(seed=7, days=40, bar_minutes=15).series("SPY")
    assert bars[1].as_of - bars[0].as_of == timedelta(minutes=15)


def test_the_config_reaches_the_feed(monkeypatch):
    monkeypatch.setenv("TRADE_BAR_TIMEFRAME", "15m")
    feed = build_feed(DataConfig(source="synthetic"))
    assert feed.bar_minutes == 15


# =========================================================================
# volume in the context
# =========================================================================
def _record(universe, cash=Decimal("25000")):
    from src.trading.models import FirmRecord

    return FirmRecord(firm_key="t", name="t", allocation=cash, cash=cash,
                      initial_allocation=cash, high_water_mark=cash,
                      risk_limit=Decimal("0.2"), universe=list(universe))


def test_a_bot_sees_volume_lined_up_with_closes():
    market = MarketData(SyntheticFeed(seed=3, days=60, bar_minutes=15), ["SPY"])
    context = adapter.build_context(_record(["SPY"]), market, [], Decimal("25000"))
    bars = context.bars("SPY", 10)
    assert [b.close for b in bars] == context.closes("SPY", 10)
    assert [b.volume for b in bars] == context.volumes("SPY", 10)
    assert all(v > 0 for v in context.volumes("SPY", 10))


# =========================================================================
# the engine, on bars built to force each decision
# =========================================================================
START = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)


def _series(symbol, drift, n=160, volume=1000.0, last=None):
    """A zig-zag around a drift: real volatility, a known direction.

    `last` overrides the final bar as (open_ratio, volume): the bar opens at
    close * open_ratio, so a ratio below one is an up bar.
    """
    bars, price = [], 100.0
    for i in range(n):
        wiggle = 0.004 if i % 2 else -0.004
        open_ = price
        price = price * math.exp(drift + wiggle)
        bars.append(Bar(symbol=symbol, as_of=START + timedelta(minutes=15 * i),
                        open=Decimal(str(round(open_, 4))), high=Decimal(str(round(max(open_, price) * 1.001, 4))),
                        low=Decimal(str(round(min(open_, price) * 0.999, 4))),
                        close=Decimal(str(round(price, 4))), volume=Decimal(str(volume))))
    if last is not None:
        ratio, vol = last
        close = bars[-1].close
        bars[-1] = Bar(symbol=symbol, as_of=bars[-1].as_of,
                       open=Decimal(str(round(float(close) * ratio, 4))),
                       high=close * Decimal("1.01"), low=close * Decimal("0.98"),
                       close=close, volume=Decimal(str(vol)))
    return bars


def _context(series: dict, held: dict | None = None, cash=Decimal("25000")):
    held = held or {}
    positions = {
        s: adapter.Holding(symbol=s, quantity=Decimal(str(q)), average_price=Decimal("100"))
        for s, q in held.items()
    }
    marks = {s: bars[-1].close for s, bars in series.items()}
    equity = cash + sum(Decimal(str(q)) * marks[s] for s, q in held.items())
    return adapter.Context(
        universe=tuple(series), cash=cash, equity=equity, as_of=START,
        _closes={s: [b.close for b in bars] for s, bars in series.items()},
        _bars=series, _marks=marks, _positions=positions,
    )


P = vol_rotation.Params(target_vol=10.0)   # no vol haircut: sizes are round numbers


def _by(orders, symbol, side):
    return [o for o in orders if o["symbol"] == symbol and o["side"] == side]


def test_the_leader_scales_in_one_tranche_on_a_bar_that_traded():
    ctx = _context({
        "AAA": _series("AAA", 0.002, last=(0.99, 1500)),     # up bar, 1.5x volume
        "BBB": _series("BBB", 0.0005, last=(0.99, 1500)),
    })
    orders = vol_rotation.propose(ctx, P)
    buys = _by(orders, "AAA", "buy")
    assert len(buys) == 1 and not _by(orders, "BBB", "buy")
    # a full position is 25% of equity, one tranche is a third of it
    assert float(buys[0]["notional"]) == pytest.approx(25000 * 0.25 / 3, rel=0.01)


def test_a_volume_surge_buys_two_tranches():
    ctx = _context({"AAA": _series("AAA", 0.002, last=(0.99, 2500))})
    buys = _by(vol_rotation.propose(ctx, P), "AAA", "buy")
    assert float(buys[0]["notional"]) == pytest.approx(25000 * 0.25 * 2 / 3, rel=0.01)


def test_a_quiet_up_bar_adds_nothing():
    ctx = _context({"AAA": _series("AAA", 0.002, last=(0.99, 900))})
    assert vol_rotation.propose(ctx, P) == []


def test_a_down_bar_adds_nothing_however_heavy():
    ctx = _context({"AAA": _series("AAA", 0.002, last=(1.01, 3000))})
    assert not _by(vol_rotation.propose(ctx, P), "AAA", "buy")


def test_a_full_position_stops_adding():
    bars = _series("AAA", 0.002, last=(0.99, 3000))
    price = float(bars[-1].close)
    full = 25000 * 0.25 / price                  # roughly three tranches' worth
    ctx = _context({"AAA": bars}, held={"AAA": full}, cash=Decimal("18750"))
    assert not _by(vol_rotation.propose(ctx, P), "AAA", "buy")


def test_a_name_rotated_out_is_sold_a_tranche_at_a_time():
    ctx = _context({
        "AAA": _series("AAA", 0.004, last=(0.99, 1000)),     # the new leader
        "BBB": _series("BBB", 0.0006, last=(0.995, 1000)),   # positive, but well behind
    }, held={"BBB": 60})
    sells = _by(vol_rotation.propose(ctx, P), "BBB", "sell")
    assert len(sells) == 1
    assert 0 < float(sells[0]["quantity"]) < 60
    assert "rotated out" in sells[0]["rationale"]


def test_heavy_volume_on_the_way_out_sells_faster():
    quiet = _context({
        "AAA": _series("AAA", 0.004),
        "BBB": _series("BBB", 0.0006, last=(0.995, 1000)),
    }, held={"BBB": 60})
    heavy = _context({
        "AAA": _series("AAA", 0.004),
        "BBB": _series("BBB", 0.0006, last=(0.995, 1600)),
    }, held={"BBB": 60})
    q = float(_by(vol_rotation.propose(quiet, P), "BBB", "sell")[0]["quantity"])
    h = float(_by(vol_rotation.propose(heavy, P), "BBB", "sell")[0]["quantity"])
    assert h > q


def test_negative_momentum_sells_everything_at_once():
    ctx = _context({"AAA": _series("AAA", -0.002)}, held={"AAA": 40})
    sells = _by(vol_rotation.propose(ctx, P), "AAA", "sell")
    assert float(sells[0]["quantity"]) == 40
    assert "all out" in sells[0]["rationale"]


def test_a_holding_is_not_churned_for_a_marginally_better_name():
    ctx = _context({
        "AAA": _series("AAA", 0.00205),
        "BBB": _series("BBB", 0.0020),
    }, held={"BBB": 20})
    assert not _by(vol_rotation.propose(ctx, P), "BBB", "sell")
    # and it is the margin doing that: take it away and BBB is rotated out
    no_margin = vol_rotation.Params(target_vol=10.0, switch_margin=0.0)
    assert _by(vol_rotation.propose(ctx, no_margin), "BBB", "sell")


def test_a_misspelt_parameter_is_refused_not_ignored():
    with pytest.raises(ValueError, match="tranche"):
        vol_rotation.Params.from_mapping({"tranche": 4})


def test_short_history_means_no_opinion():
    ctx = _context({"AAA": _series("AAA", 0.002, n=40, last=(0.99, 3000))})
    assert vol_rotation.propose(ctx, P) == []


# =========================================================================
# the ten bots
# =========================================================================
def test_there_are_ten_bots_and_each_loads():
    assert len(BOTS) == 10
    for path in BOTS:
        assert callable(adapter.load(path))


def test_the_firms_file_points_at_every_bot():
    specs = load_firm_specs(REPO / "config" / "vol_rotation.yaml")
    assert len(specs) == 10
    named = {adapter.bot_path(s.strategy).name for s in specs}
    assert named == {p.name for p in BOTS}
    for spec in specs:
        assert len(spec.universe) >= 3


def test_a_backtest_runs_the_firms_bot_not_the_pod(monkeypatch):
    """Before this, `trade backtest` quietly backtested the pod for a bot firm."""
    monkeypatch.chdir(REPO)
    feed = SyntheticFeed(seed=11, days=600, bar_minutes=15)
    universe = ["BTC-USD", "ETH-USD", "SOL-USD"]
    result = Backtester(TradingConfig(), warmup=130).run(
        "vr", universe, MarketData(feed, universe), capital=Decimal("25000"),
        risk_limit=Decimal("0.2"), strategy="bot:bots/vol_rotation/08_crypto_majors.py",
    )
    assert result.trades > 0


# =========================================================================
# take profit
# =========================================================================
from src.trading import take_profit  # noqa: E402

TP_BOTS = sorted((REPO / "bots" / "vol_rotation_tp").glob("*.py"))


def _tp(mode, **extra):
    return vol_rotation.Params(target_vol=10.0, take_profit=mode, **extra)


def _rally(n=200, drift=0.002, last=None):
    return _series("AAA", drift, n=n, last=last)


def test_there_are_ten_take_profit_iterations_and_each_loads():
    assert len(TP_BOTS) == 10
    modes = set()
    for path in TP_BOTS:
        params = adapter.load(path).__globals__["PARAMS"]
        vol_rotation.Params.from_mapping(params)        # every name is a real one
        modes.add(params["take_profit"])
    assert modes == set(take_profit.MODES)       # ten different ideas, not one ten times


def test_an_unknown_take_profit_is_refused():
    with pytest.raises(ValueError, match="take_profit"):
        vol_rotation.Params.from_mapping({"take_profit": "moon"})


def test_a_winner_short_of_its_target_ignores_the_soft_exits():
    """Negative momentum would sell this without a take profit. With one, it holds."""
    bars = _series("AAA", -0.002)
    entry = float(bars[-1].close) * 0.5              # bought far lower: deep in profit
    ctx = _context({"AAA": bars}, held={"AAA": 40})
    ctx._positions["AAA"] = adapter.Holding("AAA", Decimal("40"), Decimal(str(entry)))
    assert _by(vol_rotation.propose(ctx, P), "AAA", "sell")                 # no TP: out
    assert not _by(vol_rotation.propose(ctx, _tp("atr_target", tp_atr=1000.0)), "AAA", "sell")


def test_a_loser_still_gets_every_original_exit():
    bars = _series("AAA", -0.002)
    entry = float(bars[-1].close) * 2                # bought far higher: underwater
    ctx = _context({"AAA": bars}, held={"AAA": 40})
    ctx._positions["AAA"] = adapter.Holding("AAA", Decimal("40"), Decimal(str(entry)))
    sells = _by(vol_rotation.propose(ctx, _tp("atr_target")), "AAA", "sell")
    assert sells and "all out" in sells[0]["rationale"]


def test_a_target_that_is_reached_sells_everything():
    bars = _rally()
    entry = float(bars[-1].close) * 0.9
    ctx = _context({"AAA": bars}, held={"AAA": 40})
    ctx._positions["AAA"] = adapter.Holding("AAA", Decimal("40"), Decimal(str(entry)))
    sells = _by(vol_rotation.propose(ctx, _tp("atr_target", tp_atr=1.0)), "AAA", "sell")
    assert float(sells[0]["quantity"]) == 40 and "take profit" in sells[0]["rationale"]


def test_r_multiple_stops_out_under_the_swing_low():
    """1R is entry to the swing low, so the stop is the swing low itself."""
    bars = _series("AAA", 0.0)
    low = take_profit.lowest(bars, 128)
    entry = min(float(b.close) for b in bars[-4:])   # the trade has closed above it
    reading = vol_rotation.read("AAA", bars, P)
    held = take_profit.decide(bars, entry, reading, _tp("r_multiple"), 1000, 3000)
    assert held.sell_fraction == 0.0
    assert held.target > entry + 6 * 1.5 * take_profit.atr(bars, 32) * 0.99

    crash = Bar(symbol="AAA", as_of=bars[-1].as_of + timedelta(minutes=15),
                open=bars[-1].close, high=bars[-1].close,
                low=Decimal(str(round(low * 0.97, 4))), close=Decimal(str(round(low * 0.98, 4))),
                volume=Decimal(1000))
    broken = bars + [crash]
    stopped = take_profit.decide(broken, entry, vol_rotation.read("AAA", broken, P),
                                 _tp("r_multiple"), 1000, 3000)
    assert stopped.sell_fraction == 1.0 and "stop" in stopped.why


def test_the_ladder_takes_a_third_off_then_stops_adding():
    bars = _rally()
    a = take_profit.atr(bars, 32)
    p = _tp("ladder", tp_r=2.0, tp_stop_atr=1.0)     # rungs at 1R and 2R
    price = float(bars[-1].close)
    reading = vol_rotation.read("AAA", bars, p)
    first_rung = take_profit.decide(bars, price - 1.5 * a, reading, p, 3000, 3000)
    assert first_rung.sell_fraction == pytest.approx(1 / 3, rel=0.01)
    assert first_rung.allow_adds is False


def test_a_level_is_measured_from_before_the_run():
    """The first swing_high measured over recent bars; the rally moved its own target."""
    bars = _series("AAA", 0.0, n=100) + [
        Bar(symbol="AAA", as_of=START + timedelta(minutes=15 * (100 + i)),
            open=Decimal(100 + i), high=Decimal(101 + i), low=Decimal(99 + i),
            close=Decimal(100 + i), volume=Decimal(1000))
        for i in range(1, 30)
    ]
    base = take_profit.before_the_run(bars, entry=101.0)
    assert float(base[-1].close) <= 101.0
    assert take_profit.highest(base, 240) < take_profit.highest(bars, 240)


def test_every_rule_answers_on_real_looking_bars():
    bars = _rally(last=(0.99, 5000))
    reading = vol_rotation.read("AAA", bars, P)
    entry = float(bars[-1].close) * 0.98
    for mode in take_profit.MODES:
        decision = take_profit.decide(bars, entry, reading, _tp(mode), 3000, 3000)
        assert 0.0 <= decision.sell_fraction <= 1.0, mode
