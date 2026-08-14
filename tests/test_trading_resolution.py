"""How long a bar is, and everything that depends on knowing.

The same mistake has been made three times in this repository — the Sharpe
kill, the bar count, the borrow charge — and it is one mistake wearing three
costumes: **treating wall-clock time, or the number of times a loop went round,
as new information about the market.**

So these tests are mostly about that one confusion, asked in each of the places
it has managed to hide.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.trading.indicators import sharpe, volatility_pct
from src.trading.resolution import DAILY, RESOLUTIONS, TRADING_DAYS, parse


# =========================================================================
# the table itself
# =========================================================================
def test_every_resolution_names_itself_to_every_provider():
    """A resolution the feeds cannot ask for is a resolution that silently
    falls back to daily at the worst possible moment."""
    for name, res in RESOLUTIONS.items():
        assert res.name == name
        assert res.alpaca and res.ccxt and res.yahoo
        assert res.seconds > 0
        assert res.bars_per_day > 0


def test_a_typo_falls_back_to_daily_rather_than_exploding():
    """A bad environment variable must not stop a village that is holding
    positions — and the fallback is the slowest, most conservative bar, which
    is also what every existing deployment is already on."""
    assert parse("nonsense") is DAILY
    assert parse("") is DAILY
    assert parse(None) is DAILY
    assert parse("hourly").name == "1h"
    assert parse(" 1H ").name == "1h"


def test_the_day_is_the_default():
    assert DAILY.name == "1d"
    assert DAILY.bars_per_year == Decimal(TRADING_DAYS)
    assert DAILY.is_intraday is False
    assert parse("1h").is_intraday is True


# =========================================================================
# the Sharpe kill, asked again
# =========================================================================
def test_hourly_returns_are_not_annualised_as_if_daily():
    """The bug that killed a firm with an 80% win rate. Annualising hourly
    observations at root-252 overstates the ratio by root-6.5, and the kill
    switch reads the answer."""
    returns = [Decimal("0.001"), Decimal("-0.0005")] * 20

    as_daily = sharpe(returns)
    as_hourly = sharpe(returns, periods_per_year=parse("1h").bars_per_year)

    assert as_daily != as_hourly
    # Same observations, more of them per year: the annualised figure is larger.
    assert abs(as_hourly) > abs(as_daily)


def test_the_annualisation_scales_as_the_square_root():
    """Not a rounding difference — a scale factor, which is why getting it
    wrong is a category of bug rather than an inaccuracy."""
    returns = [Decimal("0.002"), Decimal("-0.001"), Decimal("0.003")] * 15
    daily = sharpe(returns, periods_per_year=Decimal(252))
    quadruple = sharpe(returns, periods_per_year=Decimal(252 * 4))
    assert quadruple == pytest.approx(daily * 2, rel=Decimal("0.01"))


def test_volatility_annualises_on_the_same_footing():
    returns = [Decimal("0.01"), Decimal("-0.01")] * 10
    assert volatility_pct(returns, parse("1h").bars_per_year) > volatility_pct(returns)


def test_a_nonsense_period_count_returns_no_ratio_rather_than_a_wrong_one():
    returns = [Decimal("0.01"), Decimal("-0.01")] * 10
    assert sharpe(returns, periods_per_year=Decimal(0)) is None
    assert volatility_pct(returns, Decimal(-1)) is None


# =========================================================================
# the bar count, asked again
# =========================================================================
def test_a_daily_bar_is_keyed_by_the_day():
    key = DAILY.bar_key(datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc))
    assert key == "2026-08-13"


def test_an_hourly_bar_is_keyed_by_the_hour():
    """Keying hourly bars by date would throw away six sevenths of the record,
    which is the same class of error as counting ticks — in the other
    direction."""
    hourly = parse("1h")
    ten = hourly.bar_key(datetime(2026, 8, 13, 10, 5, tzinfo=timezone.utc))
    eleven = hourly.bar_key(datetime(2026, 8, 13, 11, 5, tzinfo=timezone.utc))
    assert ten != eleven
    assert ten == "2026-08-13T10"


def test_minutes_within_an_hour_are_one_hourly_bar():
    hourly = parse("1h")
    keys = {
        hourly.bar_key(datetime(2026, 8, 13, 10, m, tzinfo=timezone.utc))
        for m in range(0, 60, 7)
    }
    assert len(keys) == 1


def test_a_minute_resolution_separates_the_minutes():
    minutes = parse("1m")
    a = minutes.bar_key(datetime(2026, 8, 13, 10, 30, tzinfo=timezone.utc))
    b = minutes.bar_key(datetime(2026, 8, 13, 10, 31, tzinfo=timezone.utc))
    assert a != b


def test_no_bar_is_never_guessed_at():
    """A blind feed has no bar. Inventing one here would let an outage build a
    track record, which is the exact opposite of what this gate is for."""
    for res in RESOLUTIONS.values():
        assert res.bar_key(None) == ""


# =========================================================================
# how much history to ask for
# =========================================================================
def test_an_intraday_feed_asks_for_days_not_years():
    """180 days of minute bars is a quarter of a million rows nobody wanted."""
    assert parse("1m").days_for_bars(200) < 10
    assert parse("1h").days_for_bars(200) < 60
    assert DAILY.days_for_bars(200) > 190


def test_asking_for_no_bars_still_asks_for_a_day():
    for res in RESOLUTIONS.values():
        assert res.days_for_bars(0) >= 1
        assert res.days_for_bars(-5) >= 1


# =========================================================================
# the borrow charge, asked again
# =========================================================================
def test_borrow_is_prorated_by_how_much_of_a_day_a_bar_is():
    """Financing accrues on the wall clock. An hourly bar is a twenty-fourth of
    a day of it, not a whole one."""
    assert DAILY.calendar_days == Decimal(1)
    assert parse("1h").calendar_days == pytest.approx(Decimal(1) / Decimal(24))
    assert parse("1m").calendar_days < parse("1h").calendar_days


# =========================================================================
# the session, and the thing it does not know
# =========================================================================
def test_the_session_length_can_be_overridden_for_a_crypto_village(monkeypatch):
    """Crypto trades around the clock, so an hourly crypto series has 24 bars a
    day and not 6.5. The default is the equity session and the override exists
    because one clock cannot be right for both."""
    hourly = parse("1h")
    assert hourly.bars_per_year == Decimal("6.5") * TRADING_DAYS
    monkeypatch.setenv("TRADE_BARS_PER_DAY", "24")
    assert hourly.bars_per_year == Decimal(24) * TRADING_DAYS


def test_a_bad_override_is_ignored_rather_than_obeyed(monkeypatch):
    monkeypatch.setenv("TRADE_BARS_PER_DAY", "not a number")
    assert parse("1h").bars_per_year == Decimal("6.5") * TRADING_DAYS


# =========================================================================
# wired through
# =========================================================================
def test_the_config_reads_it_from_the_environment(monkeypatch):
    from src.trading.config import DataConfig

    assert DataConfig().resolution is DAILY
    assert DataConfig(bar="1h").resolution.name == "1h"
    monkeypatch.setenv("TRADE_BAR", "15m")
    assert DataConfig().resolution.name == "15m"


def test_the_feeds_are_asked_in_their_own_dialect():
    from src.trading.config import DataConfig
    from src.trading.data.feeds import build_feed

    feed = build_feed(DataConfig(source="alpaca", bar="1h"))
    assert feed.timeframe == "1Hour"
    feed = build_feed(DataConfig(source="yahoo", bar="15m"))
    assert feed.interval == "15m"


def test_an_intraday_feed_does_not_ask_for_six_months_of_minutes():
    from src.trading.config import DataConfig
    from src.trading.data.feeds import build_feed

    daily = build_feed(DataConfig(source="alpaca", bar="1d", history_days=180))
    fast = build_feed(DataConfig(source="alpaca", bar="1m", history_days=180))
    assert daily.days == 180
    assert fast.days < daily.days


def test_the_scorecard_annualises_against_the_configured_bar(store, firm_record,
                                                            market_data):
    """The whole point of the wiring: the evaluator must not assume."""
    from src.trading.brokerage.evaluator import Evaluator
    from src.trading.config import DataConfig, TradingConfig

    hourly = TradingConfig(data=DataConfig(bar="1h"))
    assert Evaluator(store, hourly).config.data.resolution.bars_per_year \
        == Decimal("6.5") * TRADING_DAYS
    assert Evaluator(store, TradingConfig()).config.data.resolution.bars_per_year \
        == Decimal(TRADING_DAYS)


def test_resolution_is_the_only_place_that_knows_the_number(monkeypatch):
    """A grep, as a test. Every 252 in the trading package should come from
    here — the point of the module is that adding a bar size is a row in a
    table rather than an audit."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "trading"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("resolution.py", "indicators.py"):
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "252" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{n}")
    assert offenders == [], offenders
